from __future__ import annotations

from typing import TYPE_CHECKING

from pyagorartc.const import (
    KEEPALIVE_INTERVAL_S,
    PEER_RECOVER_COOLDOWN_S,
    PEER_RECOVER_MAX_ATTEMPTS,
    PEER_RECOVER_RESET_S,
    PEER_REJOIN_DEBOUNCE_S,
    PING_INTERVAL_S,
    PING_PONG_TIMEOUT_COUNT,
    PING_SILENCE_S,
    RENEW_TOKEN_DEBOUNCE_S,
)
from pyagorartc.session.recovery import Keepalive, PeerRecovery, PingWatchdog, RenewDebounce
from tests.unit._fakes import ManualClock

if TYPE_CHECKING:
    import pytest

PEER = 1


def recover_after_debounce(policy: PeerRecovery, clock: ManualClock) -> bool:
    """Run one peer-left the way the session does: wait the debounce, then ask."""
    delay = policy.peer_left(PEER)
    if delay is None:
        return False
    clock.advance(delay)
    return policy.should_recover(PEER, peer_present=False)


def ping_unanswered(watchdog: PingWatchdog, clock: ManualClock, ticks: int) -> list[bool]:
    """Run ``ticks`` ping ticks the way the session does, one interval apart, with no reply; each tick's verdict."""
    verdicts = []
    for n in range(ticks):
        clock.advance(PING_INTERVAL_S)
        verdicts.append(watchdog.tick())
        watchdog.sent(f"ping-{n}")
    return verdicts


def exhaust(policy: PeerRecovery, clock: ManualClock) -> None:
    for _ in range(PEER_RECOVER_MAX_ATTEMPTS):
        assert recover_after_debounce(policy, clock)
        clock.advance(PEER_RECOVER_COOLDOWN_S)


class TestPeerLeft:
    def test_waits_the_shipped_rejoin_debounce(self) -> None:
        policy = PeerRecovery(clock=ManualClock())

        assert policy.peer_left(PEER) == PEER_REJOIN_DEBOUNCE_S == 2.0

    def test_uses_the_given_now_instead_of_the_clock(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        assert recover_after_debounce(policy, clock)

        assert policy.peer_left(PEER, now=clock.now + PEER_RECOVER_COOLDOWN_S) == PEER_REJOIN_DEBOUNCE_S

    def test_declines_to_wait_while_the_cooldown_outlasts_the_debounce(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        assert recover_after_debounce(policy, clock)

        clock.advance(1.0)

        assert policy.peer_left(PEER) is None

    def test_declines_to_wait_once_the_attempt_cap_is_reached(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        exhaust(policy, clock)

        assert policy.peer_left(PEER) is None

    def test_records_nothing(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)

        policy.peer_left(PEER)

        assert policy.attempts == 0
        assert policy.should_recover(PEER, peer_present=False)

    def test_waits_again_once_the_reset_window_restores_the_budget(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        exhaust(policy, clock)

        clock.advance(PEER_RECOVER_RESET_S)

        assert policy.peer_left(PEER) == PEER_REJOIN_DEBOUNCE_S


class TestShouldRecover:
    def test_recovers_a_peer_that_did_not_rejoin(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)

        assert recover_after_debounce(policy, clock)
        assert policy.attempts == 1

    def test_does_not_recover_or_count_a_peer_that_rejoined(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        clock.advance(policy.debounce_s)

        assert not policy.should_recover(PEER, peer_present=True)
        assert policy.attempts == 0

    def test_first_recovery_is_not_blocked_by_a_cooldown_from_time_zero(self) -> None:
        clock = ManualClock(start=0.0)
        policy = PeerRecovery(clock=clock)

        assert policy.should_recover(PEER, peer_present=False)

    def test_blocks_a_second_recovery_inside_the_cooldown(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        assert policy.should_recover(PEER, peer_present=False)

        clock.advance(PEER_RECOVER_COOLDOWN_S - 0.1)

        assert not policy.should_recover(PEER, peer_present=False)
        assert policy.attempts == 1

    def test_allows_a_recovery_once_the_cooldown_has_passed(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        assert policy.should_recover(PEER, peer_present=False)

        clock.advance(PEER_RECOVER_COOLDOWN_S)

        assert policy.should_recover(PEER, peer_present=False)

    def test_gives_up_after_the_attempt_cap(self, caplog: pytest.LogCaptureFixture) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        exhaust(policy, clock)

        assert not policy.should_recover(PEER, peer_present=False)
        assert policy.attempts == PEER_RECOVER_MAX_ATTEMPTS == 5
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_a_stream_that_settled_past_the_reset_window_gets_a_fresh_budget(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        exhaust(policy, clock)

        clock.advance(PEER_RECOVER_RESET_S)

        assert policy.should_recover(PEER, peer_present=False)
        assert policy.attempts == 1

    def test_the_reset_window_is_exclusive_at_its_boundary(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(max_attempts=1, clock=clock)
        assert policy.should_recover(PEER, peer_present=False)

        clock.advance(PEER_RECOVER_RESET_S)

        assert not policy.should_recover(PEER, peer_present=False)

    def test_reset_clears_the_count_and_the_cooldown(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(clock=clock)
        exhaust(policy, clock)

        policy.reset()

        assert policy.attempts == 0
        assert policy.should_recover(PEER, peer_present=False)

    def test_honours_custom_timings(self) -> None:
        clock = ManualClock()
        policy = PeerRecovery(debounce_s=0.5, cooldown_s=1.0, max_attempts=2, reset_after_s=10.0, clock=clock)

        assert policy.peer_left(PEER) == 0.5
        assert policy.should_recover(PEER, peer_present=False)
        clock.advance(1.0)
        assert policy.should_recover(PEER, peer_present=False)
        clock.advance(1.0)
        assert not policy.should_recover(PEER, peer_present=False)
        clock.advance(10.0)
        assert policy.should_recover(PEER, peer_present=False)


class TestKeepalive:
    def test_is_due_one_interval_after_now(self) -> None:
        keepalive = Keepalive()

        assert keepalive.interval_s == KEEPALIVE_INTERVAL_S == 3.0
        assert keepalive.next_due(100.0) == 103.0
        assert keepalive.delay(100.0) == 3.0

    def test_never_reaches_a_missing_deadline(self) -> None:
        keepalive = Keepalive(interval_s=3.0)

        assert keepalive.deadline is None
        assert not keepalive.deadline_reached(1e12)

    def test_next_due_is_capped_at_the_deadline(self) -> None:
        keepalive = Keepalive(interval_s=3.0, deadline=101.0)

        assert keepalive.next_due(100.0) == 101.0
        assert keepalive.delay(100.0) == 1.0

    def test_delay_is_zero_once_the_deadline_has_passed(self) -> None:
        keepalive = Keepalive(interval_s=3.0, deadline=101.0)

        assert keepalive.delay(105.0) == 0.0

    def test_deadline_is_reached_at_and_after_its_instant(self) -> None:
        keepalive = Keepalive(interval_s=3.0, deadline=101.0)

        assert not keepalive.deadline_reached(100.999)
        assert keepalive.deadline_reached(101.0)
        assert keepalive.deadline_reached(200.0)


class TestRenewDebounce:
    def test_first_renew_is_sent(self) -> None:
        debounce = RenewDebounce(clock=ManualClock(start=0.0))

        assert debounce.should_send()

    def test_suppresses_repeats_inside_the_window(self) -> None:
        clock = ManualClock()
        debounce = RenewDebounce(clock=clock)
        assert debounce.should_send()

        clock.advance(RENEW_TOKEN_DEBOUNCE_S - 0.1)

        assert not debounce.should_send()

    def test_sends_again_once_the_window_has_passed(self) -> None:
        clock = ManualClock()
        debounce = RenewDebounce(clock=clock)
        assert debounce.should_send()

        clock.advance(RENEW_TOKEN_DEBOUNCE_S)

        assert debounce.should_send()

    def test_uses_the_given_now_instead_of_the_clock(self) -> None:
        clock = ManualClock()
        debounce = RenewDebounce(clock=clock)
        assert debounce.should_send()

        assert debounce.should_send(now=clock.now + RENEW_TOKEN_DEBOUNCE_S)

    def test_clear_lets_the_next_renew_through(self) -> None:
        clock = ManualClock()
        debounce = RenewDebounce(clock=clock)
        assert debounce.should_send()

        debounce.clear()

        assert debounce.should_send()


class TestPingWatchdog:
    def test_uses_the_sdks_ten_ticks_and_ten_seconds_of_silence(self) -> None:
        watchdog = PingWatchdog(clock=ManualClock())

        assert (watchdog.max_unanswered, watchdog.silence_s) == (PING_PONG_TIMEOUT_COUNT, PING_SILENCE_S) == (10, 10.0)

    def test_gives_up_on_the_tenth_tick_without_a_reply(self) -> None:
        clock = ManualClock()
        watchdog = PingWatchdog(clock=clock)

        verdicts = ping_unanswered(watchdog, clock, PING_PONG_TIMEOUT_COUNT)

        assert verdicts == [False] * (PING_PONG_TIMEOUT_COUNT - 1) + [True]

    def test_a_successful_reply_to_an_outstanding_ping_starts_the_count_again(self) -> None:
        clock = ManualClock()
        watchdog = PingWatchdog(clock=clock)
        ping_unanswered(watchdog, clock, PING_PONG_TIMEOUT_COUNT - 1)

        assert watchdog.reply("ping-0", ok=True)

        assert ping_unanswered(watchdog, clock, PING_PONG_TIMEOUT_COUNT - 1) == [False] * (PING_PONG_TIMEOUT_COUNT - 1)
        assert watchdog.unanswered == PING_PONG_TIMEOUT_COUNT - 1

    def test_a_failed_reply_is_matched_but_does_not_count_as_an_answer(self) -> None:
        clock = ManualClock()
        watchdog = PingWatchdog(clock=clock)
        ping_unanswered(watchdog, clock, PING_PONG_TIMEOUT_COUNT - 1)

        assert watchdog.reply("ping-0", ok=False)

        assert ping_unanswered(watchdog, clock, 1) == [True]

    def test_keeps_going_past_the_count_while_other_frames_arrive(self) -> None:
        clock = ManualClock()
        watchdog = PingWatchdog(clock=clock)
        ping_unanswered(watchdog, clock, PING_PONG_TIMEOUT_COUNT - 1)
        watchdog.frame_received()

        verdicts = ping_unanswered(watchdog, clock, 4)

        # The 10th tick is 3 s after the frame, the 13th 12 s: silence must exceed 10 s.
        assert verdicts == [False, False, False, True]

    def test_counts_silence_from_construction_until_the_first_frame(self) -> None:
        clock = ManualClock()
        watchdog = PingWatchdog(clock=clock, max_unanswered=1, silence_s=5.0)

        assert not watchdog.tick(now=clock.now + 5.0)
        assert watchdog.tick(now=clock.now + 5.1)

    def test_matches_only_ids_it_sent_and_each_once(self) -> None:
        watchdog = PingWatchdog(clock=ManualClock())
        watchdog.sent("ab7c5d")

        matched = [
            watchdog.reply("other", ok=True),
            watchdog.reply("ab7c5d", ok=True),
            watchdog.reply("ab7c5d", ok=True),
        ]

        assert matched == [False, True, False]

    def test_forgets_ids_older_than_the_count_so_a_reply_that_late_is_unmatched(self) -> None:
        clock = ManualClock()
        watchdog = PingWatchdog(clock=clock)
        ping_unanswered(watchdog, clock, PING_PONG_TIMEOUT_COUNT + 1)

        assert not watchdog.reply("ping-0", ok=True)
        assert watchdog.reply("ping-1", ok=True)
