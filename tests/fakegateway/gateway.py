"""The gateway WebSocket surface: join_v3, subscribe, ping, renew, leave, and the server events (protocol.md §2-§5)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from websockets.exceptions import ConnectionClosed

from tests.fakegateway._common import (
    CANDIDATE_PRIORITY,
    GATEWAY_UPLOAD_TYPES,
    MEDIA_PORT,
    SERVER_ICE_PWD,
    SERVER_ICE_UFRAG,
    decode,
    edge_fingerprint,
    encode,
    event,
    failed,
    success,
)
from tests.fakegateway.state import FrameRecord, JoinRejection

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from websockets.asyncio.server import ServerConnection

    from tests.fakegateway._common import JsonObject
    from tests.fakegateway.scheduler import FakeScheduler, Timer
    from tests.fakegateway.state import FakeAgoraState

# Codes from the SDK's gateway error table (protocol.md §5).
ERR_NO_AUTHORIZED = 110
ERR_REPEAT_JOIN_CHANNEL = 2003
ERR_NOT_JOINED = 2011
ERR_INVALID_VENDOR_KEY = 2013
ERR_INVALID_CHANNEL_NAME = 2014
ERR_SUBSCRIBE_REQUEST_INVALID = 2021
ERR_NOT_SUPPORTED_MESSAGE = 2022

JOIN_REQUIRED_KEYS = ("app_id", "channel_name", "channel_key", "ortc")
WILL_EXPIRE_INTERVAL_S = 1.0

_RTCP_FEEDBACKS = [
    {"type": "goog-remb"},
    {"type": "transport-cc"},
    {"type": "ccm", "parameter": "fir"},
    {"type": "nack"},
    {"type": "nack", "parameter": "pli"},
    {"type": "rrtr"},
]
_EXT_ABS_SEND_TIME = "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"
_EXT_TWCC = "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
_EXT_MID = "urn:ietf:params:rtp-hdrext:sdes:mid"
#: Sent right after every join result (``gateway/real/on_rtp_capability_change.json``).
RTP_CAPABILITIES: JsonObject = {"extmap_allow_mixed": False, "video_codec": ["H264", "VP8"], "web_av1_svc": False}
#: The join result's ``attributes`` (``gateway/real/join_ok_luba2.json``).
JOIN_ATTRIBUTES: JsonObject = {"userAttributes": {"subscribeAudioFilterTopN": 0}}


@dataclass
class GatewayConnection:
    """One client socket as the fake tracks it."""

    index: int
    ws: ServerConnection
    joined: bool = False
    uid: int | None = None
    timers: list[Timer] = field(default_factory=list)

    def cancel_timers(self) -> None:
        for timer in self.timers:
            timer.cancel()
        self.timers.clear()


def _video_codec(payload_type: int, name: str, parameters: JsonObject) -> JsonObject:
    return {
        "fmtp": {"parameters": parameters},
        "payloadType": payload_type,
        "rtcpFeedbacks": _RTCP_FEEDBACKS,
        "rtpMap": {"clockRate": 90000, "encodingName": name},
    }


def _rtx(payload_type: int, apt: int) -> JsonObject:
    return {
        "fmtp": {"parameters": {"apt": str(apt)}},
        "payloadType": payload_type,
        "rtcpFeedbacks": [{"type": "rrtr"}],
        "rtpMap": {"clockRate": 90000, "encodingName": "rtx"},
    }


def server_ortc(state: FakeAgoraState) -> JsonObject:
    """The gateway's ORTC in the join result, shaped as captured (``gateway/real/join_ok_luba2.json``).

    One ``sendrecv`` bucket with an ``rtx`` codec per video codec, the connected edge's detail-19
    fingerprint, and a host candidate on the edge's address in IPv4 and IPv6.
    """
    edge = state.gateway_edges[0]
    candidate = {"foundation": "udpcandidate", "port": MEDIA_PORT, "priority": CANDIDATE_PRIORITY, "protocol": "udp"}
    return {
        "cname": state.device.cname,
        "dtlsParameters": {
            "fingerprints": [{"algorithm": "sha-256", "fingerprint": edge_fingerprint(edge.ip, edge.port)}],
            "role": state.dtls_role,
        },
        "iceParameters": {
            "candidates": [{**candidate, "ip": ip, "type": "host"} for ip in (state.gateway_host, "::1")],
            "icePwd": SERVER_ICE_PWD,
            "iceUfrag": SERVER_ICE_UFRAG,
        },
        "rtpCapabilities": {
            "sendrecv": {
                "audioCodecs": [
                    {
                        "fmtp": {"parameters": {"minptime": "10", "useinbandfec": "1"}},
                        "payloadType": 111,
                        "rtcpFeedbacks": [{"type": "transport-cc"}, {"type": "rrtr"}, {"type": "nack"}],
                        "rtpMap": {"clockRate": 48000, "encodingName": "opus", "encodingParameters": 2},
                    }
                ],
                "audioExtensions": [
                    {"entry": 1, "extensionName": "urn:ietf:params:rtp-hdrext:ssrc-audio-level"},
                    {"entry": 2, "extensionName": _EXT_ABS_SEND_TIME},
                    {"entry": 3, "extensionName": _EXT_TWCC},
                ],
                "videoCodecs": [
                    _video_codec(
                        49, "H265", {"level-id": "180", "profile-id": "1", "tier-flag": "0", "tx-mode": "SRST"}
                    ),
                    _video_codec(96, "VP8", {}),
                    _video_codec(
                        102,
                        "H264",
                        {"level-asymmetry-allowed": "1", "packetization-mode": "1", "profile-level-id": "42e01f"},
                    ),
                    _rtx(97, 96),
                    _rtx(103, 102),
                    _rtx(50, 49),
                ],
                "videoExtensions": [
                    {"entry": 2, "extensionName": _EXT_ABS_SEND_TIME},
                    {"entry": 13, "extensionName": "urn:3gpp:video-orientation"},
                    {"entry": 3, "extensionName": _EXT_TWCC},
                    {"entry": 5, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay"},
                    {"entry": 4, "extensionName": _EXT_MID},
                    {"entry": 10, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id"},
                    {"entry": 11, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id"},
                ],
            },
        },
        "version": "2",
    }


class FakeGateway:
    """Serves the gateway protocol to every socket ``handle`` is given."""

    def __init__(self, state: FakeAgoraState, scheduler: FakeScheduler) -> None:
        self.state = state
        self.scheduler = scheduler
        self.connections: list[GatewayConnection] = []
        self._handlers: dict[str, Callable[[GatewayConnection, JsonObject], Awaitable[None]]] = {
            "join_v3": self._join,
            "subscribe": self._subscribe,
            "unsubscribe": self._ack,
            "ping": self._ping,
            "renew_token": self._renew_token,
            "set_client_role": self._ack,
            "leave": self._leave,
        }

    @property
    def joined(self) -> list[GatewayConnection]:
        return [c for c in self.connections if c.joined]

    async def handle(self, ws: ServerConnection) -> None:
        conn = GatewayConnection(index=len(self.connections), ws=ws)
        self.connections.append(conn)
        try:
            async for raw in ws:
                await self._dispatch(conn, raw)
        except ConnectionClosed:
            pass
        finally:
            conn.joined = False
            conn.cancel_timers()

    async def send(self, conn: GatewayConnection, frame: JsonObject) -> bool:
        """Send one frame and log it; ``False`` when the socket is already gone."""
        try:
            await conn.ws.send(encode(frame))
        except ConnectionClosed:
            return False
        self.state.log.record(self.state.log.gateway_sent, FrameRecord(conn.index, frame))
        return True

    async def broadcast(self, frame: JsonObject) -> None:
        """Send ``frame`` to every joined socket."""
        for conn in self.joined:
            await self.send(conn, frame)

    async def announce_peer(self, *, stream_first: bool = False) -> None:
        self.state.device_online = True
        for frame in self._peer_frames(stream_first=stream_first):
            await self.broadcast(frame)

    def _peer_frames(self, *, stream_first: bool = False) -> tuple[JsonObject, JsonObject]:
        online = event("on_user_online", {"uid": self.state.device.uid})
        stream = event("on_add_video_stream", self.state.device.video_stream())
        return (stream, online) if stream_first else (online, stream)

    async def peer_leaves(self, reason: str = "quit") -> None:
        self.state.device_online = False
        await self.broadcast(event("on_user_offline", {"uid": self.state.device.uid, "reason": reason}))

    async def send_quit(self) -> None:
        await self.broadcast(_quit_notification())

    async def send_p2p_lost(self) -> None:
        await self.broadcast(_p2p_lost())

    async def send_token_will_expire(self) -> None:
        await self.broadcast(event("on_token_privilege_will_expire"))

    async def send_token_did_expire(self) -> None:
        await self.broadcast(event("on_token_privilege_did_expire"))

    def drop_sockets(self) -> None:
        """Abort every open socket without a close handshake, like a lost network path."""
        for conn in self.connections:
            conn.joined = False
            conn.cancel_timers()
            conn.ws.transport.abort()

    async def _dispatch(self, conn: GatewayConnection, raw: str | bytes) -> None:
        frame = decode(raw)
        if frame is None:
            await self.send(conn, event("error", {"error": "frame is not a JSON object"}))
            return
        self.state.log.record(self.state.log.gateway_received, FrameRecord(conn.index, frame))
        frame_type = frame.get("_type")
        if (handler := self._handlers.get(str(frame_type))) is not None:
            await handler(conn, frame)
        elif "_id" in frame or frame_type not in GATEWAY_UPLOAD_TYPES:
            await self.send(conn, event("error", {"error": f"unknown message type {frame_type!r}"}))

    async def _ack(self, conn: GatewayConnection, frame: JsonObject) -> None:
        await self.send(conn, success(frame.get("_id")))

    async def _ping(self, conn: GatewayConnection, frame: JsonObject) -> None:
        # The captured reply carries no _message (gateway/real/ping_reply.json).
        if self.state.answer_pings:
            await self.send(conn, {"_id": frame.get("_id"), "_result": "success"})

    async def _join(self, conn: GatewayConnection, frame: JsonObject) -> None:
        message = frame.get("_message")
        if self.state.join_delay_s:
            await self.scheduler.sleep(self.state.join_delay_s)
        if (rejection := self._join_rejection(message)) is not None:
            await self.send(conn, failed(frame.get("_id"), rejection.code, rejection.message))
            return
        assert isinstance(message, dict)
        uid = _ap_uid(message) or self.state.viewer_uid
        for other in self.joined:
            if other.uid == uid:
                await self.send(other, _quit_notification())
                other.joined = False
                other.cancel_timers()
        conn.joined, conn.uid = True, uid
        await self.send(conn, success(frame.get("_id"), self._join_payload(uid)))
        if self.state.drop_socket_after_join:
            conn.joined = False
            conn.ws.transport.abort()
            return
        await self.send(conn, event("on_rtp_capability_change", dict(RTP_CAPABILITIES)))
        # A publisher already in the channel is announced by events after the result, never inside it.
        if self.state.device_online or self.state.peer_online:
            self.state.device_online = True
            for announcement in self._peer_frames():
                await self.send(conn, announcement)
        self._arm_timers(conn)

    def _join_rejection(self, message: object) -> JoinRejection | None:
        if self.state.reject_join is not None:
            return self.state.reject_join
        if not isinstance(message, dict):
            return JoinRejection(ERR_NOT_SUPPORTED_MESSAGE, "join_v3 has no _message object")
        if missing := [key for key in JOIN_REQUIRED_KEYS if key not in message]:
            return JoinRejection(ERR_NOT_SUPPORTED_MESSAGE, f"join_v3 is missing {', '.join(missing)}")
        if message["app_id"] != self.state.app_id:
            return JoinRejection(ERR_INVALID_VENDOR_KEY, "ERR_INVALID_VENDOR_KEY")
        if message["channel_name"] != self.state.channel:
            return JoinRejection(ERR_INVALID_CHANNEL_NAME, "ERR_INVALID_CHANNEL_NAME")
        if message["channel_key"] != self.state.token:
            return JoinRejection(ERR_NO_AUTHORIZED, "ERR_NO_AUTHORIZED")
        return None

    def _join_payload(self, uid: int) -> JsonObject:
        """The join result's ``_message``, keyed as captured: no ``cid``, ``cname`` or stream list."""
        return {
            "attributes": JOIN_ATTRIBUTES,
            "ortc": server_ortc(self.state),
            "rejoin_token": self.state.rejoin_token,
            "return_vosip": False,
            "uid": uid,
            "vid": self.state.vid,
        }

    def _arm_timers(self, conn: GatewayConnection) -> None:
        if (after := self.state.send_quit_after_s) is not None:
            conn.timers.append(
                self.scheduler.call_later(after, lambda: self._send_if_joined(conn, _quit_notification()))
            )
        if (after := self.state.send_p2p_lost_after_s) is not None:
            conn.timers.append(self.scheduler.call_later(after, lambda: self._send_if_joined(conn, _p2p_lost())))
        if self.state.token_will_expire:
            self._arm_will_expire(conn)

    def _arm_will_expire(self, conn: GatewayConnection) -> None:
        async def tick() -> None:
            if conn.joined and self.state.token_will_expire:
                await self.send(conn, event("on_token_privilege_will_expire"))
                self._arm_will_expire(conn)

        conn.timers.append(self.scheduler.call_later(WILL_EXPIRE_INTERVAL_S, tick))

    async def _send_if_joined(self, conn: GatewayConnection, frame: JsonObject) -> None:
        if conn.joined:
            await self.send(conn, frame)

    async def _subscribe(self, conn: GatewayConnection, frame: JsonObject) -> None:
        request_id = frame.get("_id")
        message = frame.get("_message")
        if not conn.joined:
            await self.send(conn, failed(request_id, ERR_NOT_JOINED, "ERR_NOT_JOINED"))
        elif not isinstance(message, dict) or "stream_id" not in message:
            await self.send(conn, failed(request_id, ERR_SUBSCRIBE_REQUEST_INVALID, "subscribe has no stream_id"))
        elif not self.state.device_online or message["stream_id"] != self.state.device.uid:
            await self.send(conn, failed(request_id, ERR_SUBSCRIBE_REQUEST_INVALID, "no such stream"))
        else:
            await self.send(conn, success(request_id, {"p2pid": message.get("p2p_id", 1), "uid": conn.uid}))

    async def _renew_token(self, conn: GatewayConnection, frame: JsonObject) -> None:
        message = frame.get("_message")
        if not conn.joined:
            await self.send(conn, failed(frame.get("_id"), ERR_NOT_JOINED, "ERR_NOT_JOINED"))
        elif not isinstance(message, dict) or not isinstance(message.get("token"), str) or not message["token"]:
            await self.send(conn, failed(frame.get("_id"), ERR_NO_AUTHORIZED, "renew_token has no token"))
        else:
            await self._ack(conn, frame)

    async def _leave(self, conn: GatewayConnection, frame: JsonObject) -> None:
        conn.joined = False
        conn.cancel_timers()
        with contextlib.suppress(ConnectionClosed):
            await conn.ws.close()


def _ap_uid(message: JsonObject) -> int | None:
    ap_response = message.get("ap_response")
    if isinstance(ap_response, dict) and isinstance(uid := ap_response.get("uid"), int) and uid:
        return uid
    return None


def _quit_notification() -> JsonObject:
    return event(
        "on_notification",
        {"action": "quit", "code": ERR_REPEAT_JOIN_CHANNEL, "detail": "ERR_REPEAT_JOIN", "option": ""},
    )


def _p2p_lost() -> JsonObject:
    return event("on_p2p_lost", {"error_code": 1, "error_str": "stun timeout"})
