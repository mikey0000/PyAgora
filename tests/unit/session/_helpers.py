"""Builders and doubles shared by the ``AgoraSession`` test modules: the rig, fixture frames, clock-driven sleep."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyagorartc.ap import APResponse
from pyagorartc.exceptions import GatewayConnectError
from pyagorartc.models import SessionOptions
from pyagorartc.sdp import parse_trickle_fragment
from pyagorartc.session import AgoraSession
from tests._helpers import CREDENTIALS, load_fixture, load_json_fixture
from tests.unit._fakes import FakeGatewayConnection, FakeGatewayTransport, ManualClock, ManualSleep, Recorder

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pyagorartc.models import IceCandidate

OFFER = load_fixture("sdp/chrome_recvonly_offer.sdp")
GO2RTC_OFFER = load_fixture("sdp/go2rtc_offer.sdp")
AP_RESPONSE = load_json_fixture("ap/choose_server_response.json")
SESSION_ID = "session-test"
WALL_S = 1_790_716_795.3
PUBLISHER = 1
OTHER_PUBLISHER = 2
MARKER_UID = 9
ASSIGNED_UID = 654321
STREAM_SSRC = 44444444
TIMEOUT = 1.0
START = ManualClock().now


@dataclass
class Rig:
    session: AgoraSession
    conn: FakeGatewayConnection
    transport: FakeGatewayTransport
    clock: ManualClock
    sleep: ManualSleep
    closed: Recorder
    spawned: list[asyncio.Task[None]] = field(default_factory=list)

    async def join(
        self, reply: str | dict[str, Any] = "join_ok", offer: str = OFFER, *, extra: tuple[dict[str, Any], ...] = ()
    ) -> str:
        task = asyncio.ensure_future(self.session.join(offer, SESSION_ID))
        await self.sent(1)
        frame = load_json_fixture(f"gateway/{reply}.json") if isinstance(reply, str) else reply
        self.conn.feed({**frame, "_id": self.conn.sent[0]["_id"]})
        for event in extra:
            self.conn.feed(event)
        return await asyncio.wait_for(task, TIMEOUT)

    async def sent(self, n: int) -> None:
        await asyncio.wait_for(self.conn.wait_for_sent(n), TIMEOUT)

    async def sent_type(self, frame_type: str, n: int = 1) -> list[dict[str, Any]]:
        async def arrived() -> None:
            while len(self.conn.sent_of_type(frame_type)) < n:
                await self.conn.wait_for_sent(len(self.conn.sent) + 1)

        await asyncio.wait_for(arrived(), TIMEOUT)
        return self.conn.sent_of_type(frame_type)

    async def sleepers(self, n: int) -> None:
        await asyncio.wait_for(self.sleep.wait_for_sleepers(n), TIMEOUT)

    async def mark(self, uid: int = MARKER_UID) -> None:
        """Wait until every frame fed so far, and every task they spawned, has run: a marker publisher's subscribe."""
        self.conn.feed(event("on_user_online", uid=uid))
        self.conn.feed(event("on_add_video_stream", uid=uid, ssrcId=uid))

        async def subscribed() -> None:
            while not any(f["_message"]["stream_id"] == uid for f in self.conn.sent_of_type("subscribe")):
                await self.conn.wait_for_sent(len(self.conn.sent) + 1)

        await asyncio.wait_for(subscribed(), TIMEOUT)

    def subscribes(self, uid: int = PUBLISHER) -> list[dict[str, Any]]:
        return [f for f in self.conn.sent_of_type("subscribe") if f["_message"]["stream_id"] == uid]


def rig(
    options: SessionOptions | None = None, *, refusals: int = 0, ap: dict[str, Any] | None = None, **hooks: Any
) -> Rig:
    """A session on a fake gateway whose first ``refusals`` edges refuse the connection; ``ap`` replaces the AP body."""
    conn = FakeGatewayConnection()
    transport = FakeGatewayTransport(*(GatewayConnectError("refused") for _ in range(refusals)), conn)
    clock = ManualClock()
    sleep = ManualSleep(clock)
    closed = hooks.pop("on_closed", None) or Recorder()
    spawned: list[asyncio.Task[None]] = []

    def spawn(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(coro)
        spawned.append(task)
        return task

    session = AgoraSession(
        CREDENTIALS,
        APResponse.from_api_response(AP_RESPONSE if ap is None else ap),
        options=options or SessionOptions(),
        transport=transport,
        on_closed=closed,
        clock=clock,
        wall_clock=lambda: WALL_S,
        sleep=sleep,
        spawn=spawn,
        **hooks,
    )
    return Rig(session, conn, transport, clock, sleep, closed, spawned)


def event(name: str, **message: object) -> dict[str, Any]:
    """The ``gateway/<name>.json`` fixture with ``message`` merged into its ``_message``."""
    frame = copy.deepcopy(load_json_fixture(f"gateway/{name}.json"))
    frame["_message"].update(message)
    return frame


def reply(name: str, to: dict[str, Any]) -> dict[str, Any]:
    """The ``gateway/<name>.json`` response fixture answering the sent frame ``to``."""
    return {**load_json_fixture(f"gateway/{name}.json"), "_id": to["_id"]}


def subscribe_ack(to: dict[str, Any], *, ok: bool) -> dict[str, Any]:
    """The gateway's answer to the sent ``subscribe`` ``to``: the generic success or failure response shape."""
    return reply("ping_back" if ok else "join_failed", to)


def join_ok_with_role(role: str) -> dict[str, Any]:
    frame = copy.deepcopy(load_json_fixture("gateway/join_ok.json"))
    frame["_message"]["ortc"]["dtlsParameters"]["role"] = role
    return frame


def join_ok_with_uid(uid: int) -> dict[str, Any]:
    """``join_ok`` naming ``uid`` as ours: the gateway may assign another uid than the credentials carry."""
    frame = copy.deepcopy(load_json_fixture("gateway/join_ok.json"))
    frame["_message"]["uid"] = uid
    return frame


def join_ok_without_fingerprints() -> dict[str, Any]:
    frame = copy.deepcopy(load_json_fixture("gateway/join_ok.json"))
    frame["_message"]["ortc"]["dtlsParameters"]["fingerprints"] = []
    return frame


def ap_with_fingerprints(detail_19: str | None) -> dict[str, Any]:
    """The AP fixture with the gateway block's detail 19 replaced, or removed when ``None``."""
    body = copy.deepcopy(AP_RESPONSE)
    detail = body["response_body"][0]["buffer"]["detail"]
    detail.pop("19")
    if detail_19 is not None:
        detail["19"] = detail_19
    return body


def ap_without_gateway_block() -> dict[str, Any]:
    """The AP fixture with the gateway block (flag 4096) failed, so only the TURN block remains."""
    body = copy.deepcopy(AP_RESPONSE)
    body["response_body"][0]["buffer"]["code"] = 1
    return body


def candidate_ips(join_frame: dict[str, Any]) -> list[str]:
    return [c["ip"] for c in join_frame["_message"]["ortc"]["iceParameters"]["candidates"]]


def srflx_candidate() -> IceCandidate:
    return next(
        c for c in parse_trickle_fragment(load_fixture("sdp/whep_trickle_fragment.sdpfrag")) if "srflx" in c.candidate
    )


def all_done(tasks: list[asyncio.Task[None]]) -> bool:
    return bool(tasks) and all(task.done() for task in tasks)
