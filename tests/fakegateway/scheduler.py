"""Fake time for the fake: sleeps and timers that fire only when a test advances the ``ManualClock``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import heapq
import itertools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.unit._fakes import ManualClock


@dataclass(order=True)
class Timer:
    """A pending sleep (``future``) or callback (``action``); ``cancel`` makes it never fire."""

    deadline: float
    seq: int
    action: Callable[[], Awaitable[None]] | None = field(default=None, compare=False)
    future: asyncio.Future[None] | None = field(default=None, compare=False)
    cancelled: bool = field(default=False, compare=False)

    def cancel(self) -> None:
        self.cancelled = True
        if self.future is not None and not self.future.done():
            self.future.cancel()


class FakeScheduler:
    """Orders every time-driven behaviour of the fake on one ``ManualClock``.

    ``advance`` fires due timers in deadline order, moving the clock to each deadline first,
    and awaits callbacks inline, so once it returns every frame they send has been written.
    A woken ``sleep`` resumes on the next loop turn.
    """

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self._timers: list[Timer] = []
        self._seq = itertools.count()
        self._changed = asyncio.Event()

    @property
    def pending_sleepers(self) -> int:
        return sum(1 for t in self._timers if t.future is not None and not t.cancelled and not t.future.done())

    @property
    def live_timers(self) -> int:
        """Callbacks armed and not yet fired or cancelled."""
        return sum(1 for t in self._timers if t.action is not None and not t.cancelled)

    def call_later(self, seconds: float, action: Callable[[], Awaitable[None]]) -> Timer:
        timer = Timer(self.clock() + seconds, next(self._seq), action=action)
        heapq.heappush(self._timers, timer)
        return timer

    async def sleep(self, seconds: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        timer = Timer(self.clock() + seconds, next(self._seq), future=future)
        heapq.heappush(self._timers, timer)
        self._changed.set()
        try:
            await future
        finally:
            timer.cancelled = True

    async def wait_for_sleepers(self, n: int) -> None:
        """Resolve once ``n`` coroutines are parked in ``sleep``."""
        while self.pending_sleepers < n:
            self._changed.clear()
            await self._changed.wait()

    async def advance(self, seconds: float) -> None:
        target = self.clock() + seconds
        while (timer := self._pop_due(target)) is not None:
            self.clock.now = max(self.clock.now, timer.deadline)
            if timer.future is not None:
                timer.future.set_result(None)
                await asyncio.sleep(0)
            elif timer.action is not None:
                await timer.action()
        self.clock.now = target

    def cancel_all(self) -> None:
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()

    def _pop_due(self, target: float) -> Timer | None:
        while self._timers:
            # A cancelled sleeper's future is done a loop turn before its ``finally`` marks the timer.
            if self._timers[0].cancelled or (self._timers[0].future is not None and self._timers[0].future.done()):
                heapq.heappop(self._timers)
                continue
            if self._timers[0].deadline > target:
                return None
            return heapq.heappop(self._timers)
        return None
