"""The fake Agora's world: what it accepts, who publishes, which faults are armed, and what it saw."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any, ClassVar

from tests._helpers import CREDENTIALS, RTM_CREDENTIALS
from tests.fakegateway._common import (
    CID,
    DEVICE_CNAME,
    DEVICE_PAYLOAD_TYPE,
    DEVICE_RTX_SSRC,
    DEVICE_SSRC,
    DEVICE_UID,
    LOOPBACK,
    REJOIN_TOKEN,
    TICKET,
    TURN_IPS,
    TURN_PASSWORD,
    TURN_PORT,
    VID,
)
from tests.unit._fakes import ManualClock

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakegateway._common import JsonObject


@dataclass(frozen=True)
class JoinRejection:
    """What the gateway answers every ``join_v3`` with while the ``reject_join`` knob is set."""

    code: int
    message: str


@dataclass(frozen=True)
class Edge:
    ip: str
    port: int


@dataclass
class DevicePublisher:
    """The device in the channel and its one video stream."""

    uid: int = DEVICE_UID
    ssrc: int = DEVICE_SSRC
    rtx_ssrc: int | None = DEVICE_RTX_SSRC
    payload_type: int = DEVICE_PAYLOAD_TYPE
    cname: str = DEVICE_CNAME

    def video_stream(self) -> JsonObject:
        """The ``on_add_video_stream`` payload, keyed as captured (``gateway/real/on_add_video_stream.json``)."""
        stream: JsonObject = {"cname": self.cname, "pt": self.payload_type}
        if self.rtx_ssrc is not None:
            stream["rtxSsrcId"] = self.rtx_ssrc
        return stream | {"ssrcId": self.ssrc, "uid": self.uid, "video": True}


@dataclass(frozen=True)
class FrameRecord:
    """A gateway frame and the index of the socket it travelled on (0 = first connection)."""

    connection: int
    frame: JsonObject


@dataclass(frozen=True)
class ApRequestRecord:
    """One AP POST: which host took it, what status it got, and the parsed envelope when it parsed."""

    host_index: int
    status: int
    envelope: JsonObject | None
    error: str | None = None


@dataclass(frozen=True)
class RtmRecord:
    """One RTM ``peer_messages`` POST; ``payload`` is the body's ``payload`` string decoded."""

    host_index: int
    status: int
    app_id: str
    user_id: str
    wait_for_ack: bool
    headers: Mapping[str, str]
    body: JsonObject | None
    payload: object = None
    error: str | None = None


@dataclass
class FakeLog:
    """Everything the fake received and sent, with a wake-up for tests waiting on it."""

    ap_requests: list[ApRequestRecord] = field(default_factory=list)
    rtm_messages: list[RtmRecord] = field(default_factory=list)
    gateway_received: list[FrameRecord] = field(default_factory=list)
    gateway_sent: list[FrameRecord] = field(default_factory=list)
    _changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def record(self, target: list[Any], item: object) -> None:
        target.append(item)
        self._changed.set()

    def received_of_type(self, frame_type: str) -> list[JsonObject]:
        return [r.frame for r in self.gateway_received if r.frame.get("_type") == frame_type]

    def sent_of_type(self, frame_type: str) -> list[JsonObject]:
        return [r.frame for r in self.gateway_sent if r.frame.get("_type") == frame_type]

    async def wait_until(self, predicate: Callable[[], bool]) -> None:
        """Resolve once ``predicate()`` holds; callers bound it with ``asyncio.wait_for``."""
        while not predicate():
            self._changed.clear()
            await self._changed.wait()


@dataclass
class FakeAgoraState:
    """The fake's world. Knob fields are listed in ``KNOBS`` and set through ``apply``.

    Time-driven knobs (``send_quit_after_s``, ``send_p2p_lost_after_s``, ``token_will_expire``)
    arm when a join succeeds, counting fake seconds from that join.
    """

    KNOBS: ClassVar[frozenset[str]] = frozenset(
        {
            "reject_join",
            "join_delay_s",
            "drop_socket_after_join",
            "send_quit_after_s",
            "send_p2p_lost_after_s",
            "peer_online",
            "ap_fail_hosts",
            "ap_turn_code",
            "rtm_fail_first",
            "rtm_code",
            "token_will_expire",
            "envelope_flag_order",
            "device_online",
            "dtls_role",
            "answer_pings",
        }
    )

    app_id: str = CREDENTIALS.app_id
    channel: str = CREDENTIALS.channel_name
    token: str = CREDENTIALS.token
    viewer_uid: int = CREDENTIALS.uid
    rtm_user_id: str = RTM_CREDENTIALS.user_id
    rtm_peer_user_id: str = RTM_CREDENTIALS.peer_user_id
    rtm_token: str = RTM_CREDENTIALS.token
    cid: int = CID
    vid: int = VID
    rejoin_token: str = REJOIN_TOKEN
    ticket: str = TICKET
    device: DevicePublisher = field(default_factory=DevicePublisher)
    gateway_host: str = LOOPBACK
    gateway_port: int = 0
    turn_edges: tuple[Edge, ...] = tuple(Edge(ip, TURN_PORT) for ip in TURN_IPS)
    turn_password: str = TURN_PASSWORD
    clock: ManualClock = field(default_factory=ManualClock)

    device_online: bool = True
    dtls_role: str = "client"
    answer_pings: bool = True
    reject_join: JoinRejection | None = None
    join_delay_s: float = 0.0
    drop_socket_after_join: bool = False
    send_quit_after_s: float | None = None
    send_p2p_lost_after_s: float | None = None
    peer_online: bool = False
    ap_fail_hosts: int = 0
    ap_turn_code: int = 0
    rtm_fail_first: int = 0
    rtm_code: str | None = None
    token_will_expire: bool = False
    envelope_flag_order: tuple[str, ...] = ("gateway", "turn")

    log: FakeLog = field(default_factory=FakeLog)
    rtm_posts_seen: int = 0

    @property
    def gateway_edges(self) -> tuple[Edge, ...]:
        return (Edge(self.gateway_host, self.gateway_port),)

    def apply(self, **knobs: object) -> None:
        """Set knobs by name; ``reject_join`` also takes a ``{code, message}`` mapping."""
        if unknown := set(knobs) - self.KNOBS:
            raise ValueError(f"unknown knob(s): {sorted(unknown)}")
        for name, value in knobs.items():
            match name, value:
                case "reject_join", Mapping():
                    setattr(self, name, JoinRejection(code=int(value["code"]), message=str(value["message"])))
                case "envelope_flag_order", list() | tuple():
                    setattr(self, name, tuple(str(v) for v in value))
                case _:
                    setattr(self, name, value)

    def knob_values(self) -> JsonObject:
        """The current knob settings, JSON-ready (the ``/control`` route returns this)."""
        values: JsonObject = {}
        for f in fields(self):
            if f.name in self.KNOBS:
                value = getattr(self, f.name)
                if isinstance(value, JoinRejection):
                    value = {"code": value.code, "message": value.message}
                elif isinstance(value, tuple):
                    value = list(value)
                values[f.name] = value
        return values
