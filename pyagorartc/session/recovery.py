"""Timing policies the session runtime applies: peer recovery, keep-alive cadence and renew debounce.

Pure: each policy reads an injected monotonic clock (or a ``now`` the caller passes) and owns no task;
the runtime owns every loop and timer (D13).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from pyagorartc.const import (
    KEEPALIVE_INTERVAL_S,
    PEER_RECOVER_COOLDOWN_S,
    PEER_RECOVER_MAX_ATTEMPTS,
    PEER_RECOVER_RESET_S,
    PEER_REJOIN_DEBOUNCE_S,
    RENEW_TOKEN_DEBOUNCE_S,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


class PeerRecovery:
    """When a publisher that left the channel should be recovered (D14; HA-Luba ``_peer_recovery``).

    The runtime, on ``on_user_offline`` for a uid other than its own and only when ``on_peer_left``
    is set: cancels any pending recovery timer (only the latest peer-left counts), then calls
    ``peer_left``; if that returns a delay, it sleeps that long and calls ``should_recover`` with
    whether the uid is back online, invoking ``on_peer_left(uid)`` when it returns True. It skips
    all of this once the session is no longer joined, and calls ``reset`` after a successful join.

    One budget covers every uid, as shipped: at most ``max_attempts`` recoveries, ``cooldown_s``
    apart; a stream that runs longer than ``reset_after_s`` since the last recovery earns a fresh budget.
    """

    def __init__(
        self,
        *,
        debounce_s: float = PEER_REJOIN_DEBOUNCE_S,
        cooldown_s: float = PEER_RECOVER_COOLDOWN_S,
        max_attempts: int = PEER_RECOVER_MAX_ATTEMPTS,
        reset_after_s: float = PEER_RECOVER_RESET_S,
        clock: Callable[[], float],
    ) -> None:
        self.debounce_s = debounce_s
        self.cooldown_s = cooldown_s
        self.max_attempts = max_attempts
        self.reset_after_s = reset_after_s
        self._clock = clock
        self._attempts = 0
        self._last_recovered_at: float | None = None

    @property
    def attempts(self) -> int:
        """Recoveries counted against the current budget."""
        return self._attempts

    def peer_left(self, uid: int, now: float | None = None) -> float | None:
        """Seconds to wait for ``uid`` to rejoin, or ``None`` when no recovery could follow.

        ``None`` means ``should_recover`` would refuse at the end of the debounce (attempt cap or
        cooldown), so the runtime need not arm a timer. Records nothing.
        """
        due = (self._clock() if now is None else now) + self.debounce_s
        if (reason := self._refusal(due)) is not None:
            _LOGGER.debug("Peer %s left; no recovery can follow (%s)", uid, reason)
            return None
        return self.debounce_s

    def should_recover(self, uid: int, now: float | None = None, *, peer_present: bool) -> bool:
        """Whether to recover ``uid`` now, after the debounce; records the attempt when True."""
        if peer_present:
            return False
        now = self._clock() if now is None else now
        self._apply_reset(now)
        if (reason := self._refusal(now)) is not None:
            if self._attempts >= self.max_attempts:
                _LOGGER.warning(
                    "Peer %s left the channel %s times without the stream settling; not recovering it again",
                    uid,
                    self._attempts,
                )
            else:
                _LOGGER.debug("Peer %s still gone; recovery skipped (%s)", uid, reason)
            return False
        self._last_recovered_at = now
        self._attempts += 1
        _LOGGER.debug("Recovering peer %s (attempt %s/%s)", uid, self._attempts, self.max_attempts)
        return True

    def reset(self) -> None:
        """Forget every attempt and the cooldown (a fresh session)."""
        self._attempts = 0
        self._last_recovered_at = None

    def _apply_reset(self, now: float) -> None:
        if self._attempts and self._since_last(now) > self.reset_after_s:
            self._attempts = 0

    def _refusal(self, now: float) -> str | None:
        attempts = self._attempts
        if attempts and self._since_last(now) > self.reset_after_s:
            attempts = 0
        if attempts >= self.max_attempts:
            return "attempt cap reached"
        if self._since_last(now) < self.cooldown_s:
            return "inside cooldown"
        return None

    def _since_last(self, now: float) -> float:
        return float("inf") if self._last_recovered_at is None else now - self._last_recovered_at


@dataclass(frozen=True)
class Keepalive:
    """Cadence and deadline for the host's periodic keep-alive callback (D15).

    ``deadline`` is absolute monotonic seconds; ``None`` means none. The runtime's loop: close with
    ``CloseReason.DEADLINE`` once ``deadline_reached``; otherwise run the callback (stopping the
    callback, not the deadline, when it returns False) and sleep ``delay``.
    """

    interval_s: float = KEEPALIVE_INTERVAL_S
    deadline: float | None = None

    def next_due(self, now: float) -> float:
        """The monotonic instant of the next tick: one interval on, but never past the deadline."""
        due = now + self.interval_s
        return due if self.deadline is None else min(due, self.deadline)

    def delay(self, now: float) -> float:
        """Seconds from ``now`` until ``next_due``; 0 once the deadline has passed."""
        return max(0.0, self.next_due(now) - now)

    def deadline_reached(self, now: float) -> bool:
        """Whether the session's budget is spent."""
        return self.deadline is not None and now >= self.deadline


class RenewDebounce:
    """At most one ``renew_token`` per window (D8; ``will_expire`` repeats about once a second).

    ``clear`` on ``on_token_privilege_did_expire``, or when a renew failed to send, so the next goes out.
    """

    def __init__(self, *, window_s: float = RENEW_TOKEN_DEBOUNCE_S, clock: Callable[[], float]) -> None:
        self.window_s = window_s
        self._clock = clock
        self._last_sent_at: float | None = None

    def should_send(self, now: float | None = None) -> bool:
        """Whether to send a renew now; records the send when True."""
        now = self._clock() if now is None else now
        if self._last_sent_at is not None and now - self._last_sent_at < self.window_s:
            return False
        self._last_sent_at = now
        return True

    def clear(self) -> None:
        """Let the next renew through regardless of the window."""
        self._last_sent_at = None
