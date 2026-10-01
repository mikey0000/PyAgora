from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from pyagorartc.const import (
    KEEPALIVE_INTERVAL_S,
    PING_INTERVAL_S,
    PING_PONG_TIMEOUT_COUNT,
    RENEW_TOKEN_DEBOUNCE_S,
)
from pyagorartc.exceptions import SessionClosedError
from pyagorartc.models import CloseReason, fingerprint
from tests._helpers import RENEWED_TOKEN, RTC_TOKEN, load_json_fixture
from tests.unit.session._helpers import (
    MARKER_UID,
    START,
    TIMEOUT,
    Recorder,
    event,
    reply,
    rig,
)

if TYPE_CHECKING:
    from tests.unit.session._helpers import Rig


class TestKeepalive:
    async def test_runs_at_once_and_again_only_when_the_interval_is_up(self) -> None:
        keepalive = Recorder(returns=[True])
        r = rig(keepalive=keepalive)
        await r.join()
        await asyncio.wait_for(keepalive.wait_for(1), TIMEOUT)
        await r.sleepers(2)

        r.sleep.advance(KEEPALIVE_INTERVAL_S - 0.5)
        await r.mark()

        assert len(keepalive.calls) == 1

    async def test_runs_again_once_the_interval_is_up(self) -> None:
        keepalive = Recorder(returns=[True])
        r = rig(keepalive=keepalive)
        await r.join()
        await r.sleepers(2)

        r.sleep.advance(KEEPALIVE_INTERVAL_S)
        await asyncio.wait_for(keepalive.wait_for(2), TIMEOUT)

        assert len(keepalive.calls) == 2

    async def test_stops_calling_once_the_callback_returns_false(self) -> None:
        keepalive = Recorder(returns=[True, False])
        r = rig(keepalive=keepalive)
        await r.join()
        await r.sleepers(2)
        r.sleep.advance(KEEPALIVE_INTERVAL_S)
        await asyncio.wait_for(keepalive.wait_for(2), TIMEOUT)

        r.sleep.advance(KEEPALIVE_INTERVAL_S)
        await r.mark()

        assert (len(keepalive.calls), r.session.is_joined) == (2, True)

    async def test_the_deadline_ends_the_session(self) -> None:
        keepalive = Recorder(returns=[True])
        r = rig(keepalive=keepalive, deadline=START + 5.0)
        await r.join()
        await r.sleepers(2)
        r.sleep.advance(KEEPALIVE_INTERVAL_S)
        await asyncio.wait_for(keepalive.wait_for(2), TIMEOUT)
        await r.sleepers(2)

        r.sleep.advance(KEEPALIVE_INTERVAL_S)
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert r.closed.calls == [CloseReason.DEADLINE]

    async def test_the_deadline_stops_the_callback(self) -> None:
        keepalive = Recorder(returns=[True])
        r = rig(keepalive=keepalive, deadline=START + 2.0)
        await r.join()
        await r.sleepers(2)

        r.sleep.advance(2.0)
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert len(keepalive.calls) == 1

    async def test_the_deadline_applies_without_a_keepalive_callback(self) -> None:
        r = rig(deadline=START + 5.0)
        await r.join()
        await r.sleepers(2)

        r.sleep.advance(5.0)
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert r.closed.calls == [CloseReason.DEADLINE]

    async def test_a_raising_keepalive_keeps_ticking(self) -> None:
        keepalive = Recorder(error=RuntimeError("mqtt down"))
        r = rig(keepalive=keepalive)
        await r.join()
        await r.sleepers(2)

        r.sleep.advance(KEEPALIVE_INTERVAL_S)
        await asyncio.wait_for(keepalive.wait_for(2), TIMEOUT)

        assert (len(keepalive.calls), r.session.is_joined) == (2, True)


class TestPing:
    async def test_pings_once_per_interval_after_the_join(self) -> None:
        r = rig()
        await r.join()
        await r.sleepers(1)
        r.sleep.advance(PING_INTERVAL_S)
        await r.sent_type("ping")
        await r.sleepers(1)

        r.sleep.advance(PING_INTERVAL_S)
        pings = await r.sent_type("ping", 2)

        assert len(pings) == 2
        assert len({p["_id"] for p in pings}) == 2

    async def test_stops_pinging_once_a_ping_cannot_be_sent(self) -> None:
        r = rig()
        await r.join()
        await r.sleepers(1)
        r.conn.fail_send = True
        r.sleep.advance(PING_INTERVAL_S)
        await asyncio.sleep(0)  # one loop turn: the woken ping loop tries its send
        r.conn.fail_send = False
        await r.mark()

        r.sleep.advance(PING_INTERVAL_S)
        await r.mark(MARKER_UID + 1)

        assert r.conn.sent_of_type("ping") == []


class TestPingTimeout:
    async def ping(self, r: Rig, times: int = 1) -> list[dict[str, object]]:
        """Let ``times`` ping intervals pass, each ending in a sent ping; the pings sent so far."""
        for _ in range(times):
            sent = len(r.conn.sent_of_type("ping"))
            await r.sleepers(1)
            r.sleep.advance(PING_INTERVAL_S)
            await r.sent_type("ping", sent + 1)
        return r.conn.sent_of_type("ping")

    async def test_ends_the_session_on_the_tenth_tick_without_a_reply(self) -> None:
        r = rig()
        await r.join()
        await self.ping(r, PING_PONG_TIMEOUT_COUNT - 1)
        await r.sleepers(1)

        r.sleep.advance(PING_INTERVAL_S)
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert r.closed.calls == [CloseReason.PING_TIMEOUT]
        assert len(r.conn.sent_of_type("ping")) == PING_PONG_TIMEOUT_COUNT - 1
        assert (r.conn.closed, r.conn.sent_of_type("leave")) == (True, [])

    async def test_a_reply_keeps_the_session_up(self) -> None:
        r = rig()
        await r.join()
        pings = await self.ping(r, PING_PONG_TIMEOUT_COUNT - 1)
        r.conn.feed(reply("ping_back", pings[-1]))

        await self.ping(r)

        assert (r.session.is_joined, r.closed.calls) == (True, [])

    async def test_other_gateway_frames_keep_the_session_up_without_replies(self) -> None:
        r = rig()
        await r.join()
        await self.ping(r, PING_PONG_TIMEOUT_COUNT - 1)
        r.conn.feed(event("on_rtp_capability_change"))
        await r.mark()

        await self.ping(r)

        assert (r.session.is_joined, r.closed.calls) == (True, [])

    async def test_logs_the_timeout_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join()
        await self.ping(r, PING_PONG_TIMEOUT_COUNT - 1)
        await r.sleepers(1)

        r.sleep.advance(PING_INTERVAL_S)
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert any(
            record.levelno == logging.WARNING and "answered no ping" in record.getMessage() for record in caplog.records
        )


class TestTokenRenewal:
    async def expire_soon(self, r: Rig, times: int = 1) -> None:
        for _ in range(times):
            r.conn.feed(load_json_fixture("gateway/on_token_privilege_will_expire.json"))

    async def test_renews_once_for_a_burst_of_expiry_notices(self) -> None:
        r = rig()
        await r.join()

        await self.expire_soon(r, 3)
        await r.mark()

        assert [f["_message"]["token"] for f in r.conn.sent_of_type("renew_token")] == [RTC_TOKEN]

    async def test_renews_again_once_the_debounce_window_has_passed(self) -> None:
        r = rig()
        await r.join()
        await self.expire_soon(r)
        await r.sent_type("renew_token")

        r.sleep.advance(RENEW_TOKEN_DEBOUNCE_S)
        await self.expire_soon(r)

        assert len(await r.sent_type("renew_token", 2)) == 2

    async def test_sends_the_token_the_provider_mints(self) -> None:
        provider = Recorder(returns=[RENEWED_TOKEN])
        r = rig(token_provider=provider)
        await r.join()

        await self.expire_soon(r)

        (frame,) = await r.sent_type("renew_token")
        assert frame["_message"]["token"] == RENEWED_TOKEN

    async def test_falls_back_to_the_join_token_when_the_provider_has_none(self) -> None:
        r = rig(token_provider=Recorder(returns=[None]))
        await r.join()

        await self.expire_soon(r)

        (frame,) = await r.sent_type("renew_token")
        assert frame["_message"]["token"] == RTC_TOKEN

    async def test_did_expire_lets_the_next_notice_renew_at_once(self) -> None:
        r = rig()
        await r.join()
        await self.expire_soon(r)
        await r.sent_type("renew_token")

        r.conn.feed(load_json_fixture("gateway/on_token_privilege_did_expire.json"))
        await self.expire_soon(r)

        assert len(await r.sent_type("renew_token", 2)) == 2

    async def test_an_explicit_token_is_sent_as_given(self) -> None:
        r = rig(token_provider=Recorder(returns=[RENEWED_TOKEN]))
        await r.join()

        await r.session.renew_token("rtc-token-explicit")

        assert r.conn.sent_of_type("renew_token")[0]["_message"]["token"] == "rtc-token-explicit"

    async def test_logs_a_rotation_at_info_by_fingerprint(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig(token_provider=Recorder(returns=[RENEWED_TOKEN]))
        await r.join()

        await r.session.renew_token()

        assert any(
            record.levelno == logging.INFO and fingerprint(RENEWED_TOKEN) in record.getMessage()
            for record in caplog.records
        )

    async def test_logs_no_rotation_when_the_token_is_unchanged(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig(token_provider=Recorder(returns=[RTC_TOKEN]))
        await r.join()

        await r.session.renew_token()

        assert len(r.conn.sent_of_type("renew_token")) == 1
        assert not any(fingerprint(RTC_TOKEN) in record.getMessage() for record in caplog.records)

    async def test_renewing_before_the_join_raises_session_closed(self) -> None:
        with pytest.raises(SessionClosedError):
            await rig().session.renew_token()

    async def test_a_failing_provider_lets_the_next_notice_retry(self) -> None:
        provider = Recorder(error=RuntimeError("vendor api down"))
        r = rig(token_provider=provider)
        await r.join()
        await self.expire_soon(r)
        await asyncio.wait_for(provider.wait_for(1), TIMEOUT)
        await r.mark()

        await self.expire_soon(r)
        await asyncio.wait_for(provider.wait_for(2), TIMEOUT)

        assert len(provider.calls) == 2
