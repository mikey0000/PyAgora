"""Client-side frame and request builders the integration tier sends to the fake (protocol.md shapes)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

import aiohttp
from websockets.asyncio.client import connect

from pyagora.const import EDGE_DOMAIN_SUFFIX
from pyagora.models import SessionOptions
from pyagora.session import AgoraSession, WebsocketsTransport
from tests._helpers import CREDENTIALS, RTM_CREDENTIALS, load_fixture
from tests.fakegateway._common import decode
from tests.unit._fakes import Recorder

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from websockets.asyncio.client import ClientConnection

    from pyagora.ap import APResponse
    from pyagora.models import RtmCredentials
    from pyagora.session import GatewayConnection
    from tests.fakegateway import FakeAgora
    from tests.unit._fakes import ManualClock

RECV_TIMEOUT_S = 2.0
SESSION_TIMEOUT_S = 5.0
SESSION_ID = "session-integration"
CHROME_OFFER = load_fixture("sdp/chrome_recvonly_offer.sdp")
GO2RTC_OFFER = load_fixture("sdp/go2rtc_offer.sdp")
AP_PATH = "/api/v2/transpond/webrtc?v=2"
AP_UID = 12345678

type Frame = dict[str, Any]

# Only this raw client's own frame log is silenced; library and fake loggers still reach the secret guard.
_CLIENT_WIRE_LOGGER = logging.getLogger("tests.integration.wire")
_CLIENT_WIRE_LOGGER.setLevel(logging.INFO)


def gateway_client(url: str) -> connect:
    """A raw ``websockets`` client for the fake gateway: no keepalive pings, no frame logging."""
    return connect(url, logger=_CLIENT_WIRE_LOGGER, ping_interval=None)


def ap_envelope(*, uri: int = 22, service_ids: tuple[int, ...] = (11, 26), **overrides: object) -> Frame:
    """The choose_server envelope from protocol.md §1.1, for the fixture credentials."""
    buffer: Frame = {
        "cname": CREDENTIALS.channel_name,
        "detail": {"11": "CN,GLOBAL", "17": "1", "22": "CN,GLOBAL"},
        "key": CREDENTIALS.token,
        "service_ids": list(service_ids),
        "uid": CREDENTIALS.uid,
    }
    if uri == 28:
        buffer["edges_services"] = [{"ip": "127.0.0.1", "port": 4713}]
    envelope: Frame = {
        "appid": CREDENTIALS.app_id,
        "client_ts": 1790716794173,
        "opid": 999972753885,
        "sid": "152075528",
        "request_bodies": [{"uri": uri, "buffer": buffer}],
    }
    return envelope | overrides


def ap_form(envelope: Frame | str) -> aiohttp.FormData:
    """The multipart body the client posts: one ``request`` field holding the JSON."""
    form = aiohttp.FormData()
    text = envelope if isinstance(envelope, str) else json.dumps(envelope)
    form.add_field("request", text, content_type="application/json")
    return form


def client_ortc() -> Frame:
    """The ORTC a browser offer becomes (protocol.md §2.4, trimmed to one codec per kind)."""
    return {
        "iceParameters": {"iceUfrag": "nNCO", "icePwd": "ICEPWD_REDACTED_24chars0"},
        "dtlsParameters": {
            "fingerprints": [{"hashFunction": "sha-256", "fingerprint": "69:56:A4:D2"}],
            "role": "server",
        },
        "rtpCapabilities": {
            "sendrecv": {
                "audioCodecs": [{"payloadType": 111, "rtpMap": {"encodingName": "opus", "clockRate": 48000}}],
                "audioExtensions": [{"entry": 4, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid"}],
                "videoCodecs": [{"payloadType": 96, "rtpMap": {"encodingName": "VP8", "clockRate": 90000}}],
                "videoExtensions": [{"entry": 4, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid"}],
            }
        },
        "version": "2",
    }


def join_v3(request_id: str = "a1b2c3", *, ap_uid: int = AP_UID, **overrides: object) -> Frame:
    """A ``join_v3`` request (protocol.md §2.3) for the fixture credentials; ``overrides`` replace message keys."""
    message: Frame = {
        "p2p_id": 1,
        "session_id": "0F2C9E6B4A1D4E8F9C3B2A1D0E9F8A7B",
        "app_id": CREDENTIALS.app_id,
        "channel_key": CREDENTIALS.token,
        "channel_name": CREDENTIALS.channel_name,
        "sdk_version": "4.24.3",
        "mode": "live",
        "codec": "vp8",
        "role": "host",
        "ap_response": {"code": 0, "uid": ap_uid, "cid": 123456789, "flag": 4096, "cert": "ticket-not-real"},
        "attributes": {"userAttributes": {"enableRTX": True}},
        "ortc": client_ortc(),
    }
    return {"_id": request_id, "_type": "join_v3", "_message": message | overrides}


def request(frame_type: str, message: Frame | None = None, request_id: str = "b2c3d4") -> Frame:
    """A correlated client request; ``message=None`` omits ``_message`` as ``ping`` and ``leave`` do."""
    frame: Frame = {"_id": request_id, "_type": frame_type}
    if message is not None:
        frame["_message"] = message
    return frame


def upload(frame_type: str, message: Frame) -> Frame:
    """A fire-and-forget client frame (no ``_id``)."""
    return {"_type": frame_type, "_message": message}


def subscribe(stream_id: int = 1, request_id: str = "b2c3d4") -> Frame:
    """``subscribe`` as protocol.md §3.3 records it."""
    return request(
        "subscribe",
        {
            "stream_id": stream_id,
            "stream_type": "video",
            "mode": "live",
            "codec": "vp8",
            "p2p_id": 1,
            "twcc": True,
            "rtx": False,
            "extend": "",
            "ssrcId": 44444444,
        },
        request_id,
    )


async def send(ws: ClientConnection, frame: Frame | str) -> None:
    await ws.send(frame if isinstance(frame, str) else json.dumps(frame))


async def recv_frame(ws: ClientConnection) -> Frame:
    """The next frame from the fake, decoded; bounded by ``RECV_TIMEOUT_S``."""
    raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_S)
    frame = decode(raw)
    assert frame is not None, f"fake sent a non-object frame: {raw!r}"
    return frame


def rtm_url(host: str, *, app_id: str | None = None, user_id: str | None = None, wait_for_ack: bool = False) -> str:
    path = (
        f"/dev/v2/project/{app_id or RTM_CREDENTIALS.app_id}/rtm/users/"
        f"{quote(user_id or RTM_CREDENTIALS.user_id, safe='')}/peer_messages"
    )
    return f"{host}{path}{'?wait_for_ack=true' if wait_for_ack else ''}"


def rtm_headers(credentials: RtmCredentials = RTM_CREDENTIALS) -> dict[str, str]:
    """The three Agora auth headers plus the JSON content type (protocol.md §8)."""
    return {
        "Content-Type": "application/json",
        "x-agora-token": credentials.token,
        "x-agora-uid": credentials.user_id,
        "Authorization": f"agora token={credentials.token}",
    }


def rtm_body(command: Frame | None = None, **overrides: object) -> Frame:
    """A ``peer_messages`` body; ``payload`` is the command as a compact JSON string."""
    command = command or {"cmd": "start_live", "payload": {"isSD": 0}}
    body: Frame = {
        "destination": RTM_CREDENTIALS.peer_user_id,
        "enable_offline_messaging": False,
        "enable_historical_messaging": False,
        "payload": json.dumps(command, separators=(",", ":")),
    }
    return body | overrides


def loopback_url(edge_url: str) -> str:
    """``wss://a-b-c-d.<edge domain>:port`` as ``ws://a.b.c.d:port``, the address the fake gateway serves."""
    parts = urlsplit(edge_url)
    host = parts.hostname or ""
    assert parts.scheme == "wss", f"the session dialled {edge_url!r}, not a wss edge URL"
    assert host.endswith(EDGE_DOMAIN_SUFFIX), f"the session dialled {edge_url!r}, not an edge host"
    return f"ws://{host.removesuffix(EDGE_DOMAIN_SUFFIX).replace('-', '.')}:{parts.port}"


class LoopbackEdgeTransport:
    """The real ``WebsocketsTransport``, dialling each edge URL at the loopback address it names.

    Stands in for the edge's DNS name and certificate only: the fake gateway speaks plain ``ws``, so ``ssl_for``
    gives no TLS context. ``urls`` and ``verify_ssl`` record what the session asked for.
    """

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.verify_ssl: list[bool] = []
        self._inner = WebsocketsTransport()

    async def connect(self, url: str, *, timeout_s: float, verify_ssl: bool) -> GatewayConnection:
        self.urls.append(url)
        self.verify_ssl.append(verify_ssl)
        return await self._inner.connect(loopback_url(url), timeout_s=timeout_s, verify_ssl=verify_ssl)


@dataclass
class SessionRig:
    """A real ``AgoraSession`` on the fake Agora, sharing its fake time; ``spawned`` holds every task it started."""

    session: AgoraSession
    agora: FakeAgora
    transport: LoopbackEdgeTransport
    closed: Recorder
    spawned: list[asyncio.Task[None]] = field(default_factory=list)

    async def join(self, offer: str = CHROME_OFFER) -> str:
        return await asyncio.wait_for(self.session.join(offer, SESSION_ID), SESSION_TIMEOUT_S)

    async def received(self, frame_type: str, n: int = 1) -> list[Frame]:
        """Every ``frame_type`` frame the fake has received, once there are at least ``n``."""
        log = self.agora.state.log
        await asyncio.wait_for(log.wait_until(lambda: len(log.received_of_type(frame_type)) >= n), SESSION_TIMEOUT_S)
        return log.received_of_type(frame_type)

    async def sleepers(self, n: int) -> None:
        """Wait until ``n`` coroutines, the session's and the fake's together, sleep on the fake clock."""
        await asyncio.wait_for(self.agora.scheduler.wait_for_sleepers(n), SESSION_TIMEOUT_S)

    async def advance(self, seconds: float) -> None:
        await asyncio.wait_for(self.agora.scheduler.advance(seconds), SESSION_TIMEOUT_S)

    async def ended(self) -> None:
        await asyncio.wait_for(self.closed.wait_for(1), SESSION_TIMEOUT_S)

    async def mark(self) -> None:
        """Wait until the session has read every frame the fake sent so far: the device's announcement, subscribed.

        Needs the device offline (``device_online=False``) and not yet announced.
        """
        await self.agora.announce_peer()
        await self.received("subscribe")


def session_rig(
    agora: FakeAgora, clock: ManualClock, ap: APResponse, options: SessionOptions | None = None, **hooks: Any
) -> SessionRig:
    """An ``AgoraSession`` whose clock, sleep and wall clock are the fake's, over ``LoopbackEdgeTransport``."""
    transport = LoopbackEdgeTransport()
    closed = hooks.pop("on_closed", None) or Recorder()
    spawned: list[asyncio.Task[None]] = []

    def spawn(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(coro)
        spawned.append(task)
        return task

    session = AgoraSession(
        CREDENTIALS,
        ap,
        options=options or SessionOptions(),
        transport=transport,
        on_closed=closed,
        clock=clock,
        wall_clock=clock,
        sleep=agora.scheduler.sleep,
        spawn=spawn,
        **hooks,
    )
    return SessionRig(session, agora, transport, closed, spawned)
