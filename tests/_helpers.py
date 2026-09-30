"""Builders and fixture values shared across test tiers: credentials, fixture loading, secrets."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pyagorartc.models import ChannelCredentials, RtmCredentials

FIXTURES = Path(__file__).parent / "fixtures"

APP_ID = "app-id-test"
CHANNEL = "channel-test"
RTC_TOKEN = "rtc-token-not-real"
RTM_TOKEN = "rtm-token-not-real"
RENEWED_TOKEN = "rtc-token-renewed"
UID = 123456
TURN_CREDENTIAL = "turn-credential-not-real"
TICKET = "ticket-not-real"
ENCRYPTION_SECRET = "aes-key-not-real"
CREDENTIALS = ChannelCredentials(app_id=APP_ID, channel_name=CHANNEL, token=RTC_TOKEN, uid=UID)
RTM_CREDENTIALS = RtmCredentials(app_id=APP_ID, user_id="app_123456", peer_user_id="dev_654321", token=RTM_TOKEN)

#: Every fixture value that Constitution §6 says must never reach a log line or a repr.
SECRET_VALUES: tuple[str, ...] = (
    RTC_TOKEN,
    RTM_TOKEN,
    RENEWED_TOKEN,
    TURN_CREDENTIAL,
    TICKET,
    ENCRYPTION_SECRET,
    "TICKET_REDACTED",
    "TURN_TICKET_REDACTED",
    "TURN_CRED_REDACTED",
    "turn-pass-not-real",  # the fake gateway's TURN password
    "rejoin-token-not-real",  # the fake gateway's rejoin token
    "ticket-not-real",  # the fake gateway's ticket
    # ICE passwords (Constitution §6): the fake gateway's, then the fixtures'
    "fake-server-ice-pwd-0000",
    "ice-pwd-gateway-test-001",
    "ice-pwd-offer-test-00001",
    "ICEPWD_REDACTED_24chars0",
    "ICEPWD_REDACTED_24chars1",
    "not-a-real-ice-password",
)


def leaked_secrets(records: list[logging.LogRecord]) -> list[str]:
    """The fixture secrets that appear in any record's message or exception text."""
    texts = [r.getMessage() + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "") for r in records]
    return [value for value in SECRET_VALUES if any(value in text for text in texts)]


def load_fixture(relative: str) -> str:
    """The text of ``tests/fixtures/<relative>``."""
    return (FIXTURES / relative).read_text()


def load_json_fixture(relative: str) -> Any:
    """``tests/fixtures/<relative>`` decoded from JSON."""
    return json.loads(load_fixture(relative))
