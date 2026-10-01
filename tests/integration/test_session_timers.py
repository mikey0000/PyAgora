"""The session's timers on the fake Agora's clock: token renewal, keep-alive, deadline and ping."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pyagorartc.const import KEEPALIVE_INTERVAL_S, PING_INTERVAL_S, PING_PONG_TIMEOUT_COUNT, RENEW_TOKEN_DEBOUNCE_S
from pyagorartc.models import CloseReason
from tests._helpers import RENEWED_TOKEN, RTC_TOKEN
from tests.fakegateway.gateway import WILL_EXPIRE_INTERVAL_S
from tests.integration._helpers import SESSION_TIMEOUT_S
from tests.unit._fakes import Recorder

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import SessionRig
    from tests.unit._fakes import ManualClock


def _tokens(fake_agora: FakeAgora) -> list[object]:
    return [frame["_message"]["token"] for frame in fake_agora.state.log.received_of_type("renew_token")]


class TestTokenRenewal:
    async def test_renews_once_per_debounce_window_with_the_providers_token(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(token_will_expire=True, device_online=False)
        r = new_session(token_provider=Recorder(returns=[RENEWED_TOKEN]))
        await r.join()

        await r.advance(RENEW_TOKEN_DEBOUNCE_S - WILL_EXPIRE_INTERVAL_S)
        await r.mark()

        assert len(fake_agora.state.log.sent_of_type("on_token_privilege_will_expire")) > 1
        assert _tokens(fake_agora) == [RENEWED_TOKEN]

    async def test_renews_again_once_the_window_has_passed(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(token_will_expire=True)
        r = new_session(token_provider=Recorder(returns=[RENEWED_TOKEN]))
        await r.join()
        await r.advance(WILL_EXPIRE_INTERVAL_S)
        await r.received("renew_token")

        await r.advance(RENEW_TOKEN_DEBOUNCE_S)

        await r.received("renew_token", 2)
        assert _tokens(fake_agora) == [RENEWED_TOKEN, RENEWED_TOKEN]

    async def test_renews_with_the_join_token_without_a_provider(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(token_will_expire=True)
        r = new_session()
        await r.join()

        await r.advance(WILL_EXPIRE_INTERVAL_S)

        await r.received("renew_token")
        assert _tokens(fake_agora) == [RTC_TOKEN]

    async def test_a_did_expire_lets_the_next_will_expire_renew_inside_the_window(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()
        await fake_agora.send_token_will_expire()
        await r.received("renew_token")

        await fake_agora.send_token_did_expire()
        await fake_agora.send_token_will_expire()

        await r.received("renew_token", 2)


class TestKeepalive:
    async def test_ticks_on_fake_time_and_stops_once_the_callback_returns_false(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(device_online=False)
        keepalive = Recorder(returns=[True, False])
        r = new_session(keepalive=keepalive)
        await r.join()
        await asyncio.wait_for(keepalive.wait_for(1), SESSION_TIMEOUT_S)
        await r.sleepers(2)

        await r.advance(KEEPALIVE_INTERVAL_S)
        await asyncio.wait_for(keepalive.wait_for(2), SESSION_TIMEOUT_S)
        await r.advance(KEEPALIVE_INTERVAL_S * 5)
        await r.mark()

        assert len(keepalive.calls) == 2
        assert r.session.is_joined

    async def test_the_deadline_ends_the_session(
        self, fake_clock: ManualClock, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session(deadline=fake_clock() + 5.0)
        await r.join()
        await r.sleepers(2)

        await r.advance(5.0)
        await r.ended()

        assert r.closed.calls == [CloseReason.DEADLINE]


class TestPing:
    async def test_pings_every_interval_of_fake_time(self, new_session: Callable[..., SessionRig]) -> None:
        r = new_session()
        await r.join()
        await r.sleepers(1)

        await r.advance(PING_INTERVAL_S)
        await r.received("ping")
        await r.sleepers(1)
        await r.advance(PING_INTERVAL_S)

        assert len(await r.received("ping", 2)) == 2

    async def test_a_gateway_that_stops_answering_pings_ends_the_session_with_ping_timeout(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(answer_pings=False)
        r = new_session()
        await r.join()
        for sent in range(1, PING_PONG_TIMEOUT_COUNT):
            await r.sleepers(1)
            await r.advance(PING_INTERVAL_S)
            await r.received("ping", sent)
        await r.sleepers(1)

        await r.advance(PING_INTERVAL_S)
        await r.ended()

        assert r.closed.calls == [CloseReason.PING_TIMEOUT]
        assert len(fake_agora.state.log.received_of_type("ping")) == PING_PONG_TIMEOUT_COUNT - 1

    async def test_answered_pings_keep_the_session_up_past_the_timeout(
        self, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        for sent in range(1, PING_PONG_TIMEOUT_COUNT + 1):
            await r.sleepers(1)
            await r.advance(PING_INTERVAL_S)
            await r.received("ping", sent)

        assert (r.session.is_joined, r.closed.calls) == (True, [])
