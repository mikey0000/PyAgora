"""Host-neutral value types shared by every layer (D2, D19).

Secrets (tokens, keys, credentials) are redacted in ``repr``; ``fingerprint`` is the only
form that may reach a log line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import hashlib
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, overload

from pyagorartc.const import (
    DEFAULT_AREA_CODE,
    DEFAULT_CLIENT_CODEC,
    DEFAULT_ORTC_DTLS_ROLE,
    GATEWAY_CONNECT_TIMEOUT_S,
    JOIN_TIMEOUT_S,
    RENEW_TOKEN_DEBOUNCE_S,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_FINGERPRINT_CHARS = 12
_INTEGER_TEXT = re.compile(r"[+-]?[0-9]+")


def fingerprint(secret: str) -> str:
    """A short, non-reversible handle for a secret, safe for log lines."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:_FINGERPRINT_CHARS]


@overload
def as_int(value: object) -> int | None: ...
@overload
def as_int(value: object, default: int) -> int: ...
def as_int(value: object, default: int | None = None) -> int | None:
    """``value`` as a wire integer: an int (not a bool) or ASCII digits with an optional sign, else ``default``.

    The one coercion rule for every wire integer, so the AP and gateway parsers agree on ``"12"`` and ``True``.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(text := value.strip()):
        return int(text)
    return default


@dataclass(frozen=True, kw_only=True, repr=False)
class ChannelEncryption:
    """Agora channel encryption parameters; modelled but never sent in the join (D20, Q17).

    ``mode`` is the SDK's name (``aes-256-gcm2``); ``salt`` is the raw bytes, already
    base64-decoded by the host. Setting it only makes the join log a WARNING.
    """

    mode: str
    secret: str
    salt: bytes | None = None

    def __repr__(self) -> str:
        return f"ChannelEncryption(mode={self.mode!r}, secret=<redacted>, salt={'<present>' if self.salt else None})"


@dataclass(frozen=True, kw_only=True, repr=False)
class ChannelCredentials:
    """Everything needed to join one Agora channel, whichever vendor minted it.

    Mapping: Mammotion ``appid/channelName/token/uid/areaCode/license``; PetKit
    ``AGORA_APP_ID/channel_id/rtc_token/uid``. Token refresh is a callback on the session,
    so this stays immutable.
    """

    app_id: str
    channel_name: str
    token: str
    uid: int
    string_uid: str | None = None
    area_code: str = DEFAULT_AREA_CODE
    license: str | None = None
    encryption: ChannelEncryption | None = None

    def __repr__(self) -> str:
        return (
            f"ChannelCredentials(app_id={fingerprint(self.app_id)!s}, channel_name={self.channel_name!r}, "
            f"token=<{fingerprint(self.token)}>, uid={self.uid!r}, string_uid={self.string_uid!r}, "
            f"area_code={self.area_code!r}, license={'<present>' if self.license else None}, "
            f"encryption={self.encryption!r})"
        )


@dataclass(frozen=True, kw_only=True, repr=False)
class RtmCredentials:
    """Credentials for Agora RTM peer messages (D18)."""

    app_id: str
    user_id: str
    peer_user_id: str
    token: str

    def __repr__(self) -> str:
        return (
            f"RtmCredentials(app_id={fingerprint(self.app_id)}, user_id={self.user_id!r}, "
            f"peer_user_id={self.peer_user_id!r}, token=<{fingerprint(self.token)}>)"
        )


@dataclass(frozen=True)
class IceCandidate:
    """One ICE candidate as a viewer reports it (``candidate:`` line without the ``a=`` prefix)."""

    candidate: str
    sdp_mid: str | None = None
    sdp_mline_index: int | None = None


@dataclass(frozen=True)
class EdgeAddress:
    """One gateway or TURN edge from an access-point response."""

    ip: str
    port: int
    username: str | None = None
    credentials: str | None = None
    ticket: str | None = None
    fingerprint: str | None = None

    def __repr__(self) -> str:
        return (
            f"EdgeAddress(ip={self.ip!r}, port={self.port!r}, username={self.username!r}, "
            f"credentials={'<redacted>' if self.credentials else None}, ticket={'<present>' if self.ticket else None}, "
            f"fingerprint={self.fingerprint!r})"
        )


@dataclass(frozen=True, repr=False)
class ICEServer:
    """An ICE server entry in the shape a WebRTC consumer expects (``urls``, ``username``, ``credential``)."""

    urls: list[str]
    username: str | None = None
    credential: str | None = None

    def __repr__(self) -> str:
        return f"ICEServer(urls={self.urls!r}, username={self.username!r}, credential={'<redacted>' if self.credential else None})"


class TurnMode(IntEnum):
    """Which TURN transports ``get_ice_servers`` emits (the SDK's ``new_turn_mode``)."""

    ALL = 1
    UDP_ONLY = 2
    TCP_ONLY = 3
    TLS_ONLY = 4


class TurnCredentialStrategy(Enum):
    """Where TURN username/credential come from (D12)."""

    UID = "uid"  # username = str(uid), credential = derive_password(uid)
    DETAIL_FIRST = "detail_first"  # AP detail 8/4 when present, else UID


class CloseReason(Enum):
    """Why a session ended; delivered once to ``on_closed`` (D14)."""

    CLOSED_BY_HOST = "closed_by_host"
    GATEWAY_QUIT = "gateway_quit"
    P2P_LOST = "p2p_lost"
    SOCKET_CLOSED = "socket_closed"
    DEADLINE = "deadline"
    JOIN_FAILED = "join_failed"


@dataclass(frozen=True)
class RemoteStream:
    """A publisher's video stream as announced by the gateway (``on_add_video_stream``)."""

    uid: int
    ssrc: int
    rtx_ssrc: int | None = None
    codec: str | None = None
    payload_type: int | None = None
    cname: str | None = None


@dataclass(frozen=True, kw_only=True)
class SessionOptions:
    """Per-session knobs; defaults preserve the Mammotion behaviour that shipped (architecture §4).

    ``extra_join_attributes`` merges into the join's ``userAttributes`` last; it is stored read-only and left
    out of the hash, so the options stay hashable. ``end_on_p2p_lost``
    is off because the Mammotion integration deliberately ignored ``on_p2p_lost`` (D22);
    PetKit ends the session on it. ``prealloc_pc`` and ``renew_debounce_s`` exist so a host can
    answer Q10 and Q11 without patching.
    """

    client_codec: str = DEFAULT_CLIENT_CODEC
    target_uid: int | None = None
    send_set_client_role: bool = False
    ortc_dtls_role: str | None = DEFAULT_ORTC_DTLS_ROLE
    instant_video: bool = False
    declare_remote_video_ssrc: bool = False
    disable_audio: bool = False
    subscribe_retry_attempts: int = 0
    subscribe_retry_delay_s: float = 0.0
    strip_mid_extension: bool = True
    prealloc_pc: bool = True
    end_on_p2p_lost: bool = False
    renew_debounce_s: float = RENEW_TOKEN_DEBOUNCE_S
    join_timeout_s: float = JOIN_TIMEOUT_S
    connect_timeout_s: float = GATEWAY_CONNECT_TIMEOUT_S
    verify_ssl: bool = True
    extra_join_attributes: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}), hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_join_attributes", MappingProxyType(dict(self.extra_join_attributes)))
