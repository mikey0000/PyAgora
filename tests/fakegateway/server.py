"""``FakeAgora``: the gateway WebSocket server plus the AP/RTM/control HTTP app, on loopback ephemeral ports."""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING, Self

from aiohttp import web
from websockets.asyncio.server import serve

from tests.fakegateway import ap, rtm
from tests.fakegateway._common import HOST_PORTS_KEY, LOOPBACK, decode
from tests.fakegateway.gateway import FakeGateway
from tests.fakegateway.scheduler import FakeScheduler
from tests.fakegateway.state import FakeAgoraState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    from websockets.asyncio.server import Server

    from tests.fakegateway._common import JsonObject

CONTROL_ROUTE = "/control"
DEFAULT_AP_HOST_COUNT = 4
DEFAULT_RTM_HOST_COUNT = 2

# websockets logs every frame at DEBUG, join tokens included; keep it above the secret guard's reach.
_WIRE_LOGGER = logging.getLogger("tests.fakegateway.wire")
_WIRE_LOGGER.setLevel(logging.INFO)


class FakeAgora:
    """An in-process Agora: ``async with FakeAgora() as agora:`` then point clients at its URLs.

    ``ap_hosts`` and ``rtm_hosts`` are distinct base URLs (one loopback port each), so a
    client's host rotation is observable in ``state.log`` by ``host_index``. The gateway is
    one plain ``ws://`` socket; every AP gateway edge points at it.
    """

    def __init__(
        self,
        state: FakeAgoraState | None = None,
        *,
        ap_host_count: int = DEFAULT_AP_HOST_COUNT,
        rtm_host_count: int = DEFAULT_RTM_HOST_COUNT,
    ) -> None:
        self.state = state or FakeAgoraState()
        self.scheduler = FakeScheduler(self.state.clock)
        self.gateway = FakeGateway(self.state, self.scheduler)
        self._ap_host_count = ap_host_count
        self._rtm_host_count = rtm_host_count
        self._ws_server: Server | None = None
        self._runner: web.AppRunner | None = None
        self.ap_hosts: list[str] = []
        self.rtm_hosts: list[str] = []
        self.control_url = ""

    @property
    def gateway_url(self) -> str:
        return f"ws://{self.state.gateway_host}:{self.state.gateway_port}"

    async def __aenter__(self) -> Self:
        self._ws_server = await serve(
            self.gateway.handle, LOOPBACK, 0, ping_interval=None, compression=None, logger=_WIRE_LOGGER
        )
        self.state.gateway_host = LOOPBACK
        self.state.gateway_port = self._ws_server.sockets[0].getsockname()[1]

        app = web.Application()
        ap.add_routes(app, self.state)
        rtm.add_routes(app, self.state)
        app.router.add_post(CONTROL_ROUTE, self._control_route)
        sockets = [_bound_socket() for _ in range(self._ap_host_count + self._rtm_host_count)]
        ports = [s.getsockname()[1] for s in sockets]
        app[HOST_PORTS_KEY] = {port: i for i, port in enumerate(ports[: self._ap_host_count])} | {
            port: i for i, port in enumerate(ports[self._ap_host_count :])
        }
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        for sock in sockets:
            await web.SockSite(self._runner, sock).start()
        urls = [f"http://{LOOPBACK}:{port}" for port in ports]
        self.ap_hosts, self.rtm_hosts = urls[: self._ap_host_count], urls[self._ap_host_count :]
        self.control_url = f"{urls[0]}{CONTROL_ROUTE}"
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.scheduler.cancel_all()
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
        if self._runner is not None:
            await self._runner.cleanup()

    def control(self, **knobs: object) -> None:
        """Set fault knobs by name (``FakeAgoraState.KNOBS``); unknown names raise ``ValueError``."""
        self.state.apply(**knobs)

    async def announce_peer(self, *, stream_first: bool = False) -> None:
        """The device joins: ``on_user_online`` and ``on_add_video_stream`` to every joined socket."""
        await self.gateway.announce_peer(stream_first=stream_first)

    async def peer_leaves(self, reason: str = "quit") -> None:
        await self.gateway.peer_leaves(reason)

    async def send_quit(self) -> None:
        """``on_notification`` quit 2003, as when a second viewer joins on the same uid."""
        await self.gateway.send_quit()

    async def send_p2p_lost(self) -> None:
        await self.gateway.send_p2p_lost()

    async def send_token_will_expire(self) -> None:
        await self.gateway.send_token_will_expire()

    async def send_token_did_expire(self) -> None:
        await self.gateway.send_token_did_expire()

    def drop_socket(self) -> None:
        """Abort every gateway socket without a close frame."""
        self.gateway.drop_sockets()

    async def advance(self, seconds: float) -> None:
        """Move the fake clock, firing every time-driven knob and delayed reply that falls due."""
        await self.scheduler.advance(seconds)

    async def _control_route(self, request: web.Request) -> web.Response:
        """``{"knobs": {...}, "action": name, "args": {...}}``; returns the knob values after both."""
        body = decode(await request.read())
        if body is None:
            return web.json_response({"error": "body is not a JSON object"}, status=400)
        try:
            self.control(**body.get("knobs", {}))
            if (action := body.get("action")) is not None:
                await self._actions()[action](body.get("args", {}))
        except (KeyError, TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"knobs": self.state.knob_values()})

    def _actions(self) -> dict[str, Callable[[JsonObject], Awaitable[None]]]:
        async def drop(_: JsonObject) -> None:
            self.drop_socket()

        return {
            "announce_peer": lambda args: self.announce_peer(stream_first=bool(args.get("stream_first"))),
            "peer_leaves": lambda args: self.peer_leaves(str(args.get("reason", "quit"))),
            "send_quit": lambda _: self.send_quit(),
            "send_p2p_lost": lambda _: self.send_p2p_lost(),
            "send_token_will_expire": lambda _: self.send_token_will_expire(),
            "send_token_did_expire": lambda _: self.send_token_did_expire(),
            "drop_socket": drop,
            "advance": lambda args: self.advance(float(args["seconds"])),
        }


def _bound_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOOPBACK, 0))
    return sock
