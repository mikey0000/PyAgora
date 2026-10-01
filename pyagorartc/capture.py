"""Opt-in capture of every wire exchange, redacted, for turning a live session into fixtures.

The ``pyagorartc.capture`` logger is silent unless a host enables it at DEBUG. When it is
enabled, every gateway frame, access-point exchange and RTM exchange is logged as one
compact JSON line with credential-bearing values replaced by ``<redacted>``, so a debug
session against real infrastructure yields the frame bodies ``docs/protocol.md`` lacks
(D30) without ever writing a secret to disk.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

CAPTURE_LOGGER = logging.getLogger("pyagorartc.capture")

#: JSON keys (compared case-insensitively) whose values are credentials or keys.
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "channel_key",
        "token",
        "rtc_token",
        "rtm_token",
        "rejoin_token",
        "cert",
        "ticket",
        "key",
        "sign",
        "signature",
        "aes_secret",
        "aes_salt",
        "icepwd",
        "ice_pwd",
        "credentials",
        "credential",
        "password",
        "license",
        "app_id",
        "appid",
        "client_secret",
        "x-agora-token",
        "authorization",
    }
)

REDACTED = "<redacted>"


#: Access-point ``detail`` keys holding secrets: ``4`` (TURN password) and ``10`` (an AccessToken2-shaped value).
AP_DETAIL_SECRET_KEYS: frozenset[str] = frozenset({"4", "10"})


def redact(value: object, *, in_detail: bool = False) -> object:
    """Return a copy of a JSON-like value with every credential-bearing key's value replaced."""
    if isinstance(value, dict):
        return {
            str(k): (
                REDACTED
                if (str(k).lower() in REDACTED_KEYS or (in_detail and str(k) in AP_DETAIL_SECRET_KEYS))
                and v not in (None, "")
                else redact(v, in_detail=str(k) == "detail")
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item, in_detail=in_detail) for item in value]
    return value


def capture(source: str, direction: str, payload: Mapping[str, object] | list[object] | str) -> None:
    """Log one exchange when capture is enabled: ``source`` (gateway/ap/rtm), ``direction`` (in/out), redacted body."""
    if not CAPTURE_LOGGER.isEnabledFor(logging.DEBUG):
        return
    if isinstance(payload, str):
        try:
            body: object = json.loads(payload)
        except ValueError:
            CAPTURE_LOGGER.debug("%s %s raw=%s", source, direction, payload[:2000])
            return
    else:
        body = payload
    CAPTURE_LOGGER.debug("%s %s %s", source, direction, json.dumps(redact(body), separators=(",", ":")))
