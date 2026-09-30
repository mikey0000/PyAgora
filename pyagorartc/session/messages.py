"""Builders and parsers for every gateway WebSocket frame; the only module that spells a gateway wire key.

Pure: no I/O and no clock reads. Timestamps and ids are parameters; ``new_request_id`` and
``new_process_id`` are the one place ids are minted. Shapes and provenance: ``docs/protocol.md`` §2 to §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import logging
import secrets
from typing import TYPE_CHECKING, NamedTuple, TypeIs

from pyagorartc.const import SDK_VERSION
from pyagorartc.exceptions import JoinRejectedError
from pyagorartc.models import RemoteStream, as_int
from pyagorartc.sdp import offers_rtx

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyagorartc.models import ChannelCredentials, SessionOptions

_LOGGER = logging.getLogger(__name__)

type JsonObject = dict[str, object]

# shipped (HA-Luba and PetKit); the SDK sends navigator.userAgent.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
DEFAULT_P2P_ID = 1
STREAM_MODE = "live"
JOIN_ROLE = "host"
RESULT_SUCCESS = "success"
RESULT_FAILED = "failed"
NOTIFICATION_QUIT = "quit"
NO_PAYLOAD_TYPE = 0
_REQUEST_ID_BYTES = 3

# shipped (HA-Luba's flag set); nested under userAttributes per the SDK (D7). enableInstantVideo is set per session.
_USER_ATTRIBUTES: Mapping[str, object] = {
    "enableAudioMetadata": False,
    "enableAudioPts": False,
    "enableNetworkQualityProbe": False,
    "enablePublishedUserList": True,
    "enableUserList": False,
    "maxSubscription": 50,
    "enableUserLicenseCheck": True,
    "enableRTX": True,
    "enableInstantVideo": False,
    "enableDataStream2": False,
    "enableAutFeedback": True,
    "enableUserAutoRebalanceCheck": True,
    "enableXR": True,
    "enableLossbasedBwe": True,
    "enableAutCC": True,
    "enablePreallocPC": True,
    "enablePubTWCC": False,
    "enableSubTWCC": True,
    "enablePubRTX": True,
    "enableSubRTX": True,
    "enableVosFallback": False,
    "enableQualityFallback": False,
    "enableDualStreamFlag": False,
}

# PetKit's markers for "this node in the join payload is a video stream" (the server's key is unverified).
_VIDEO_CODEC_MARKERS = frozenset({"h264", "h265", "video"})
# Deeper than any payload the SDK describes; bounds the walk on a hostile or broken frame.
_MAX_STREAM_SEARCH_DEPTH = 32


class FrameType(StrEnum):
    """Every gateway frame ``_type`` the library builds or recognises (SDK:28390-28474)."""

    JOIN_V3 = "join_v3"
    REJOIN_V3 = "rejoin_v3"
    LEAVE = "leave"
    PING = "ping"
    PING_BACK = "ping_back"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    RENEW_TOKEN = "renew_token"  # noqa: S105 - a frame name, not a secret
    SET_CLIENT_ROLE = "set_client_role"
    ANSWER = "answer"
    ERROR = "error"
    ON_USER_ONLINE = "on_user_online"
    ON_USER_OFFLINE = "on_user_offline"
    ON_STREAM_FALLBACK_UPDATE = "on_stream_fallback_update"
    ON_PUBLISH_STREAM = "on_publish_stream"
    ON_UPLINK_STATS = "on_uplink_stats"
    ON_P2P_LOST = "on_p2p_lost"
    ON_P2P_OK = "on_p2p_ok"
    ON_REMOVE_STREAM = "on_remove_stream"
    ON_ADD_AUDIO_STREAM = "on_add_audio_stream"
    ON_ADD_VIDEO_STREAM = "on_add_video_stream"
    ON_TOKEN_PRIVILEGE_WILL_EXPIRE = "on_token_privilege_will_expire"  # noqa: S105 - a frame name, not a secret
    ON_TOKEN_PRIVILEGE_DID_EXPIRE = "on_token_privilege_did_expire"  # noqa: S105 - a frame name, not a secret
    ON_USER_BANNED = "on_user_banned"
    ON_USER_LICENSE_BANNED = "on_user_license_banned"
    ON_NOTIFICATION = "on_notification"
    ON_CRYPT_ERROR = "on_crypt_error"
    MUTE_AUDIO = "mute_audio"
    MUTE_VIDEO = "mute_video"
    UNMUTE_AUDIO = "unmute_audio"
    UNMUTE_VIDEO = "unmute_video"
    RECEIVE_METADATA = "receive_metadata"
    ON_DATA_STREAM = "on_data_stream"
    ON_RTP_CAPABILITY_CHANGE = "on_rtp_capability_change"
    ON_REMOTE_DATASTREAM_UPDATE = "on_remote_datastream_update"
    ON_REMOTE_FULL_DATASTREAM_INFO = "on_remote_full_datastream_info"
    ENABLE_LOCAL_VIDEO = "enable_local_video"
    DISABLE_LOCAL_VIDEO = "disable_local_video"
    ENABLE_LOCAL_AUDIO = "enable_local_audio"
    DISABLE_LOCAL_AUDIO = "disable_local_audio"
    ON_PUBLISHED_USER_LIST = "on_published_user_list"
    ENABLE_MULTI_STREAM = "enable_multi_stream"
    ON_USER_LIST = "on_user_list"


@dataclass(frozen=True, repr=False)
class GatewayFrame:
    """One decoded gateway frame (protocol.md §2.1).

    ``type`` is the raw ``_type`` string (compare it with ``FrameType``; unknown names are kept),
    ``message`` is ``_message`` or ``{}`` when absent or not an object, and ``raw`` is the whole
    decoded object. ``repr`` shows only the envelope, since payloads can carry ICE passwords and
    rejoin tokens.
    """

    id: str | None
    type: str | None
    result: str | None
    message: Mapping[str, object] = field(default_factory=dict)
    raw: Mapping[str, object] = field(default_factory=dict)

    @property
    def is_response(self) -> bool:
        """A frame with ``_id`` answers a request, even if it also carries ``_type`` (SDK dispatch)."""
        return self.id is not None

    @property
    def is_event(self) -> bool:
        """A server-initiated event: no ``_id``."""
        return self.id is None

    @property
    def ok(self) -> bool:
        """Whether this is a response whose ``_result`` is ``success``."""
        return self.is_response and self.result == RESULT_SUCCESS

    def __repr__(self) -> str:
        return (
            f"GatewayFrame(id={self.id!r}, type={self.type!r}, result={self.result!r}, "
            f"message_keys={sorted(self.message)!r})"
        )


@dataclass(frozen=True, kw_only=True, repr=False)
class JoinResult:
    """What the session reads from a successful ``join_v3`` response (protocol.md §2.5)."""

    ortc: Mapping[str, object]
    rejoin_token: str | None
    cid: int | None
    uid: int | None
    vid: int | None
    cname: str | None
    existing_streams: list[RemoteStream]

    @property
    def offers_rtx(self) -> bool:
        """Whether the gateway ORTC lists an ``rtx`` video codec; the value for ``subscribe.rtx`` (``sdp.offers_rtx``)."""
        return offers_rtx(self.ortc)

    def __repr__(self) -> str:
        return (
            f"JoinResult(uid={self.uid!r}, cid={self.cid!r}, vid={self.vid!r}, cname={self.cname!r}, "
            f"rejoin_token={'<present>' if self.rejoin_token else None}, "
            f"existing_streams={self.existing_streams!r})"
        )


class UserEvent(NamedTuple):
    """``on_user_online`` / ``on_user_offline``; ``reason`` values are unverified."""

    uid: int
    reason: str | None


class Notification(NamedTuple):
    """``on_notification``: ``action`` is ``quit``, ``recover``, ``retry``, ``warn``…; ``code`` per protocol.md §5."""

    action: str | None
    code: int | None
    detail: str | None


class P2PLost(NamedTuple):
    """``on_p2p_lost``."""

    code: int | None
    error: str | None


class P2POk(NamedTuple):
    """``on_p2p_ok``; ``uid`` should equal the join uid."""

    uid: int | None
    proxy: bool


class RtpCapabilities(NamedTuple):
    """``on_rtp_capability_change``."""

    video_codecs: tuple[str, ...]
    extmap_allow_mixed: bool
    web_av1_svc: bool


class ErrorInfo(NamedTuple):
    """The code and text of an ``error`` event or a ``failed`` response."""

    code: int | None
    message: str | None


def new_request_id() -> str:
    """A fresh 6-character ``_id`` (the SDK's ``qO(6,"")``; shipped ``secrets.token_hex(3)``)."""
    return secrets.token_hex(_REQUEST_ID_BYTES)


def new_process_id() -> str:
    """A fresh ``process_id`` in the SDK's ``process-<8>-<4>-<4>-<4>-<12>`` shape."""
    groups = (secrets.token_hex(n) for n in (4, 2, 2, 2, 6))
    return "process-" + "-".join(groups)


def encode_frame(frame: Mapping[str, object]) -> str:
    """The text frame to send."""
    return json.dumps(frame)


def build_join(  # noqa: PLR0913 - every per-session join input is explicit so the builder stays pure
    creds: ChannelCredentials,
    ortc: Mapping[str, object],
    ap_response: Mapping[str, object],
    *,
    options: SessionOptions,
    session_id: str,
    process_id: str,
    client_ts_ms: int,
    request_id: str,
    p2p_id: int = DEFAULT_P2P_ID,
) -> JsonObject:
    """The ``join_v3`` request (D7, D20).

    Channel encryption is never sent: the SDK RSA-wraps the secret with a key it embeds (D20, Q17), so
    ``creds.encryption`` only logs a WARNING.

    Args:
        creds: The channel to join; ``license`` and ``string_uid`` are sent only when set.
        ortc: The client ORTC from ``sdp.offer_to_ortc`` with candidates already merged (D11).
        ap_response: The flag-4096 AP block in its join form (``ticket`` set, ``server_ts``).
        options: ``client_codec``, ``instant_video`` and ``extra_join_attributes`` (merged into
            ``userAttributes`` last, so they override the shipped flags) are read.
        session_id: The viewer's session id.
        process_id: From ``new_process_id``; stable for the session.
        client_ts_ms: Wall-clock milliseconds, sent as ``join_ts``.
        request_id: The frame ``_id``; the join response echoes it.
        p2p_id: The SDK's peer-connection counter; 1 for the first and only connection.

    """
    user_attributes = {
        **_USER_ATTRIBUTES,
        "enableInstantVideo": options.instant_video,
        "enablePreallocPC": options.prealloc_pc,
        **options.extra_join_attributes,
    }
    message: JsonObject = {
        "p2p_id": p2p_id,
        "session_id": session_id,
        "app_id": creds.app_id,
        "channel_key": creds.token,
        "channel_name": creds.channel_name,
        "sdk_version": SDK_VERSION,
        "browser": BROWSER_USER_AGENT,
        "process_id": process_id,
        "mode": STREAM_MODE,
        "codec": options.client_codec,
        "role": JOIN_ROLE,
        "has_changed_gateway": False,
        "ap_response": dict(ap_response),
        "extend": "",
        "details": {"6": creds.string_uid} if creds.string_uid else {},
        "features": {"rejoin": True},
        "attributes": {"userAttributes": user_attributes},
        "join_ts": client_ts_ms,
        "ortc": ortc,
    }
    if creds.license:
        message["license"] = creds.license
    if creds.string_uid:
        message["string_uid"] = creds.string_uid
    if creds.encryption is not None:
        _LOGGER.warning(
            "Channel encryption (%s) is not supported; joining without aes_* fields (D20)", creds.encryption.mode
        )
    return _request(FrameType.JOIN_V3, request_id, message)


def build_subscribe(
    stream: RemoteStream,
    *,
    codec: str,
    rtx: bool,
    request_id: str,
    p2p_id: int = DEFAULT_P2P_ID,
    twcc: bool = True,
) -> JsonObject:
    """A ``subscribe`` for one remote video stream.

    ``codec`` must be the join's ``client_codec`` (the SDK sends ``spec.codec`` in both); ``rtx`` is
    ``JoinResult.offers_rtx``, because the gateway relays RTX only on payload types it offered.
    """
    return _request(
        FrameType.SUBSCRIBE,
        request_id,
        {
            "stream_id": stream.uid,
            "stream_type": "video",
            "mode": STREAM_MODE,
            "codec": codec,
            "p2p_id": p2p_id,
            "twcc": twcc,
            "rtx": rtx,
            "extend": "",
            "ssrcId": stream.ssrc,
        },
    )


def build_unsubscribe(stream_id: int, *, request_id: str, p2p_id: int = DEFAULT_P2P_ID) -> JsonObject:
    """An ``unsubscribe`` for the stream published by ``stream_id`` (the publisher's uid)."""
    return _request(FrameType.UNSUBSCRIBE, request_id, {"p2p_id": p2p_id, "ortc": [], "stream_id": stream_id})


def build_ping(request_id: str) -> JsonObject:
    """A ``ping``; it carries no ``_message``."""
    return {"_id": request_id, "_type": FrameType.PING.value}


def build_leave(request_id: str) -> JsonObject:
    """A ``leave``; send only after a successful join, then close the socket."""
    return {"_id": request_id, "_type": FrameType.LEAVE.value}


def build_renew_token(token: str, *, request_id: str) -> JsonObject:
    """A ``renew_token`` carrying ``token`` (the provider's new token or the join token, D8)."""
    return _request(FrameType.RENEW_TOKEN, request_id, {"token": token})


def build_set_client_role(role: str, level: int, *, client_ts_ms: int, request_id: str) -> JsonObject:
    """A ``set_client_role``; sent only when ``SessionOptions.send_set_client_role`` is on (D6)."""
    return _request(FrameType.SET_CLIENT_ROLE, request_id, {"role": role, "level": level, "client_ts": client_ts_ms})


def parse_frame(text: str) -> GatewayFrame | None:
    """Decode one text frame, or ``None`` when it is not a JSON object.

    Unknown ``_type`` values and extra keys are kept, never rejected (Constitution §7); the caller
    logs a ``None`` or an unhandled type once at DEBUG and moves on.
    """
    try:
        decoded = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(decoded, dict):
        return None
    return _frame_from(decoded)


def _frame_from(decoded: Mapping[str, object]) -> GatewayFrame:
    message = decoded.get("_message")
    return GatewayFrame(
        id=_as_str(decoded.get("_id")),
        type=_as_str(decoded.get("_type")),
        result=_as_str(decoded.get("_result")),
        message=message if isinstance(message, dict) else {},
        raw=decoded,
    )


def parse_join_result(frame: GatewayFrame) -> JoinResult:
    """Read a ``join_v3`` response.

    Raises:
        JoinRejectedError: The frame is not a success response (``code`` from ``error_code`` or
            ``code``, as the SDK reads it), or the success carries no ``ortc`` object.

    """
    if not frame.ok:
        if frame.result == RESULT_FAILED:
            error = parse_error(frame)
            raise JoinRejectedError(error.code, error.message or "join failed")
        raise JoinRejectedError(None, f"join response has result {frame.result!r}")
    message = frame.message
    ortc = message.get("ortc")
    if not isinstance(ortc, dict) or not ortc:
        raise JoinRejectedError(None, "join response has no 'ortc'")
    return JoinResult(
        ortc=ortc,
        rejoin_token=_as_str(message.get("rejoin_token")),
        cid=as_int(message.get("cid")),
        uid=as_int(message.get("uid")),
        vid=as_int(message.get("vid")),
        cname=_as_str(message.get("cname")),
        existing_streams=existing_streams_from_join(message),
    )


def describe_frame(frame: GatewayFrame | Mapping[str, object]) -> str:
    """A log-safe one-line outline of ``frame`` (decoded, or a built frame about to be sent): type, id, keys only."""
    if not isinstance(frame, GatewayFrame):
        frame = _frame_from(frame)
    return f"type={frame.type} id={frame.id} message_keys={sorted(frame.message)}"


def existing_streams_from_join(message: Mapping[str, object]) -> list[RemoteStream]:
    """Video streams already published when we joined, found anywhere in the join payload.

    PetKit's walk (``_find_existing_video_streams``): any object with an int ``uid`` and ``ssrcId``
    plus a video marker, at most 32 levels deep. Deduplicated by ``(uid, ssrc)`` in payload order. The
    server's key is unverified.
    """
    found: dict[tuple[int, int], RemoteStream] = {}
    _collect_video_streams(message, found)
    return list(found.values())


def parse_remote_stream(message: Mapping[str, object]) -> RemoteStream | None:
    """An ``on_add_video_stream`` payload, or ``None`` without an int ``uid`` and ``ssrcId``.

    The event type implies video, so no ``video`` flag is required (HA-Luba). ``codec`` is lower-cased.
    A ``pt`` of 0 is logged at WARNING: the gateway found no offered codec it can relay the stream as.
    """
    uid = as_int(message.get("uid"))
    ssrc = as_int(message.get("ssrcId"))
    if uid is None or ssrc is None:
        return None
    stream = _stream_from(message, uid, ssrc)
    if lacks_payload_type(stream):
        _LOGGER.warning(
            "Gateway negotiated no video payload type for uid %s (pt=0, codec %s): the offer carried no codec "
            "this stream can be relayed as; an H265 publisher needs a viewer with HEVC decode",
            uid,
            stream.codec,
        )
    return stream


def lacks_payload_type(stream: RemoteStream) -> bool:
    """Whether the gateway announced the stream with ``pt`` 0, so it will decode to nothing."""
    return stream.payload_type == NO_PAYLOAD_TYPE


def parse_user_event(message: Mapping[str, object]) -> UserEvent | None:
    """An ``on_user_online`` / ``on_user_offline`` payload, or ``None`` without a uid."""
    if (uid := as_int(message.get("uid"))) is None:
        return None
    return UserEvent(uid, _as_str(message.get("reason")))


def parse_notification(message: Mapping[str, object]) -> Notification:
    """An ``on_notification`` payload."""
    return Notification(_as_str(message.get("action")), as_int(message.get("code")), _as_str(message.get("detail")))


def is_quit(notification: Notification) -> bool:
    """Whether the gateway has quit this session (``action: quit``, e.g. code 2003 repeat join)."""
    return notification.action == NOTIFICATION_QUIT


def parse_p2p_lost(frame: GatewayFrame) -> P2PLost:
    """An ``on_p2p_lost`` event, read from ``_message`` (shipped code read the top level, protocol.md §5).

    Each field falls back to the top level only when ``_message`` lacks it, until a capture settles it.
    """
    code = as_int(frame.message.get("error_code"))
    error = _as_str(frame.message.get("error_str"))
    return P2PLost(
        code if code is not None else as_int(frame.raw.get("error_code")),
        error if error is not None else _as_str(frame.raw.get("error_str")),
    )


def parse_p2p_ok(message: Mapping[str, object]) -> P2POk:
    """An ``on_p2p_ok`` payload."""
    return P2POk(as_int(message.get("uid")), message.get("proxy") is True)


def parse_rtp_capability_change(message: Mapping[str, object]) -> RtpCapabilities:
    """An ``on_rtp_capability_change`` payload; non-string codec entries are dropped."""
    codecs = message.get("video_codec")
    return RtpCapabilities(
        tuple(c for c in codecs if isinstance(c, str)) if isinstance(codecs, list) else (),
        message.get("extmap_allow_mixed") is True,
        message.get("web_av1_svc") is True,
    )


def parse_error(frame: GatewayFrame) -> ErrorInfo:
    """The error carried by an ``error`` event (``error``) or a failed response (``error_code || code``, ``error_str``)."""
    message = frame.message
    code = as_int(message.get("error_code")) or as_int(message.get("code"))
    return ErrorInfo(code, _as_str(message.get("error_str")) or _as_str(message.get("error")))


def _request(frame_type: FrameType, request_id: str, message: JsonObject) -> JsonObject:
    return {"_id": request_id, "_type": frame_type.value, "_message": message}


def _collect_video_streams(node: object, found: dict[tuple[int, int], RemoteStream], depth: int = 0) -> None:
    if depth > _MAX_STREAM_SEARCH_DEPTH:
        _LOGGER.debug("Join payload nests deeper than %s levels; not searched further", _MAX_STREAM_SEARCH_DEPTH)
        return
    if isinstance(node, dict):
        if stream := _existing_video_stream(node):
            found.setdefault((stream.uid, stream.ssrc), stream)
        for value in node.values():
            _collect_video_streams(value, found, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _collect_video_streams(item, found, depth + 1)


def _existing_video_stream(node: Mapping[str, object]) -> RemoteStream | None:
    has_video_marker = (
        node.get("video") is True
        or node.get("stream_type") == "video"
        or node.get("codec") in _VIDEO_CODEC_MARKERS
        or node.get("rtxSsrcId") is not None
    )
    uid, ssrc = node.get("uid"), node.get("ssrcId")
    if not has_video_marker or not _is_int(uid) or not _is_int(ssrc):
        return None
    return _stream_from(node, uid, ssrc)


def _stream_from(message: Mapping[str, object], uid: int, ssrc: int) -> RemoteStream:
    codec = _as_str(message.get("codec"))
    return RemoteStream(
        uid=uid,
        ssrc=ssrc,
        rtx_ssrc=as_int(message.get("rtxSsrcId")),
        codec=codec.lower() if codec else None,
        payload_type=as_int(message.get("pt")),
        cname=_as_str(message.get("cname")),
    )


def _is_int(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
