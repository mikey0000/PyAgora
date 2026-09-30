"""Wire helpers and fixture-default values shared by the fake's protocol modules."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Callable

type JsonObject = dict[str, Any]

LOOPBACK = "127.0.0.1"
DEVICE_UID = 1
DEVICE_SSRC = 44444444
DEVICE_RTX_SSRC = 44444445
DEVICE_CNAME = "fake-device-cname"
DEVICE_CODEC = "h264"
DEVICE_PAYLOAD_TYPE = 102
CID = 123456789
VID = 987654
REJOIN_TOKEN = "rejoin-token-not-real"
TICKET = "ticket-not-real"
TURN_USERNAME = "turn-user-test"
TURN_PASSWORD = "turn-pass-not-real"  # AP detail "4" (D12); obviously fake
TURN_IPS: tuple[str, ...] = ("203.0.113.10", "203.0.113.11", "203.0.113.12")  # RFC 5737 TEST-NET-3
TURN_PORT = 443
MEDIA_PORT = 4707
CANDIDATE_PRIORITY = 2103266323
SERVER_ICE_UFRAG = "FkUf"
SERVER_ICE_PWD = "fake-server-ice-pwd-0000"  # 24 chars, like the real ones

FLAG_GATEWAY = 4096
FLAG_TURN = 4194310
#: AP request service id → response block name and flag (protocol.md §1.2).
SERVICE_BLOCKS: dict[int, tuple[str, int]] = {11: ("gateway", FLAG_GATEWAY), 26: ("turn", FLAG_TURN)}

GATEWAY_UPLOAD_TYPES = frozenset(
    {"ping_back", "wrtc_stats", "ws_inflate_data_length", "denoiser_stats", "extension_usage_stats"}
)


def encode(frame: JsonObject) -> str:
    """One wire frame as compact JSON text."""
    return json.dumps(frame, separators=(",", ":"))


def decode(text: str | bytes) -> JsonObject | None:
    """A frame decoded from JSON, or ``None`` when it is not a JSON object."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def now_ms(clock: Callable[[], float]) -> int:
    """The fake clock in milliseconds, the unit Agora timestamps use."""
    return int(clock() * 1000)


def dtls_fingerprint(seed: str) -> str:
    """A deterministic 32-byte, colon-separated sha-256 fingerprint."""
    digest = hashlib.sha256(seed.encode()).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, 64, 2))


def success(request_id: object, message: JsonObject | None = None) -> JsonObject:
    """A correlated success response (protocol.md §2.1)."""
    return {"_id": request_id, "_result": "success", "_message": message or {}}


def failed(request_id: object, code: int, error: str) -> JsonObject:
    """A correlated failure in the shape the SDK reads (protocol.md §5)."""
    return {"_id": request_id, "_result": "failed", "_message": {"error_code": code, "error_str": error}}


def event(event_type: str, message: JsonObject | None = None) -> JsonObject:
    """An uncorrelated server event."""
    return {"_type": event_type, "_message": message or {}}


#: Local port → host index, for sites that stand in for distinct Agora hosts.
HOST_PORTS_KEY: web.AppKey[dict[int, int]] = web.AppKey("host_ports", dict)


def host_index(request: web.Request) -> int:
    """Which of the fake's hosts (0-based, in ``ap_hosts``/``rtm_hosts`` order) took ``request``."""
    sockname = request.transport.get_extra_info("sockname") if request.transport else None
    return request.app[HOST_PORTS_KEY].get(sockname[1] if sockname else -1, -1)
