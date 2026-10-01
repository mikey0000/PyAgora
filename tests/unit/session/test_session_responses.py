"""``AgoraSession`` correlating gateway responses with the requests it sent: ping replies, subscribe acks, strays."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyagorartc.const import PING_INTERVAL_S
from pyagorartc.models import SessionOptions
from tests.unit.session._helpers import PUBLISHER, announced, reply, rig, subscribe_ack

if TYPE_CHECKING:
    import pytest

STRAY = "matches no pending request"


def strays(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if STRAY in record.getMessage()]


class TestPingReplies:
    async def test_a_reply_to_a_ping_resolves_it_without_a_log_line(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join()
        await r.sleepers(1)
        r.sleep.advance(PING_INTERVAL_S)
        (ping,) = await r.sent_type("ping")
        caplog.clear()

        r.conn.feed(reply("ping_back", ping))
        await r.mark()

        assert strays(caplog) == []


class TestSubscribeAcks:
    async def test_the_captured_ack_resolves_the_subscribe(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join(extra=announced())
        (subscribe,) = await r.sent_type("subscribe")

        r.conn.feed(subscribe_ack(subscribe, ok=True))
        await r.mark()

        assert strays(caplog) == []

    async def test_a_refused_subscribe_logs_a_warning_with_the_code_and_keeps_the_session(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        r = rig()
        await r.join(extra=announced())
        (subscribe,) = await r.sent_type("subscribe")

        r.conn.feed(subscribe_ack(subscribe, ok=False))
        await r.mark()

        (warning,) = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING and "refused the subscribe" in record.getMessage()
        ]
        assert str(PUBLISHER) in warning.getMessage()
        assert "2003" in warning.getMessage()
        assert (r.session.is_joined, r.closed.calls, strays(caplog)) == (True, [], [])

    async def test_the_last_retry_is_tracked_too(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig(SessionOptions(subscribe_retry_attempts=1, subscribe_retry_delay_s=1.0))
        await r.join(extra=announced())
        await r.sent_type("subscribe")
        await r.sleepers(2)
        r.sleep.advance(1.0)
        last = (await r.sent_type("subscribe", 2))[1]

        r.conn.feed(subscribe_ack(last, ok=True))
        await r.mark()

        assert strays(caplog) == []


class TestStrayResponses:
    async def test_a_response_to_nothing_sent_is_logged_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join()

        r.conn.feed(reply("ping_back", {"_id": "fedcba"}))
        await r.mark()

        (record,) = strays(caplog)
        assert record.levelno == logging.DEBUG
        assert r.session.is_joined
