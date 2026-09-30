"""Hand-written fakes for every seam the unit tier replaces (docs/testing.md §3, D21).

``FakeGatewayTransport`` hands out scripted ``FakeGatewayConnection``s. A connection's
incoming frames are a queue the test feeds; sent frames are recorded and decoded; ``gate``
holds ``recv`` open until set. Misuse raises ``FakeMisuseError`` (a ``BaseException``) so
code under test mapping ``except Exception`` cannot swallow it.
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

from pyagora.exceptions import GatewayConnectError

__all__ = [
    "FakeGatewayConnection",
    "FakeGatewayTransport",
    "FakeMisuseError",
    "ManualClock",
    "ManualSleep",
    "Recorder",
    "RecordingHttpSession",
]


class FakeMisuseError(BaseException):
    """A test drove a fake outside its script; never catchable by the code under test."""


class ManualClock:
    """A monotonic clock the test advances; ``__call__`` returns the current value."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ManualSleep:
    """``asyncio.sleep`` on a ``ManualClock``: sleepers wake only when ``advance`` passes their deadline."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []
        self._changed = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        entry = (self.clock() + seconds, asyncio.get_running_loop().create_future())
        self._sleepers.append(entry)
        self._changed.set()
        try:
            await entry[1]
        finally:
            self._sleepers.remove(entry)

    @property
    def pending(self) -> int:
        return len(self._sleepers)

    async def wait_for_sleepers(self, n: int) -> None:
        while self.pending < n:
            self._changed.clear()
            await self._changed.wait()

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        for due, future in list(self._sleepers):
            if due <= self.clock() and not future.done():
                future.set_result(None)


class Recorder:
    """A host callback that records each call's argument and answers from ``returns`` (the last one repeats)."""

    def __init__(self, *, returns: Sequence[object] = (), error: Exception | None = None) -> None:
        self.calls: list[object] = []
        self._results = list(returns)
        self._error = error
        self._changed = asyncio.Event()

    async def __call__(self, *args: object) -> Any:
        assert len(args) <= 1, f"host callbacks take at most one argument, got {args!r}"
        self.calls.append(args[0] if args else None)
        self._changed.set()
        if self._error is not None:
            raise self._error
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0] if self._results else None

    async def wait_for(self, n: int) -> None:
        while len(self.calls) < n:
            self._changed.clear()
            await self._changed.wait()


class FakeGatewayConnection:
    """Implements ``GatewayConnection``.

    ``feed(frame)`` queues an incoming frame (a dict is JSON-encoded); ``recv`` waits for
    one. ``sent`` holds every frame the session sent, decoded. ``fail_send`` makes the
    next send raise ``GatewayConnectError``; ``send_error`` makes the next send raise it
    instead, once. While ``send_gate`` is an unset event, every send waits on it
    (``wait_for_held_sends(n)`` resolves once ``n`` are waiting). ``close()`` from the test
    side makes ``recv`` raise ``GatewayConnectError`` once the queue drains, like a peer
    closing the socket.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.sent_raw: list[str] = []
        self.closed = False
        self.fail_send = False
        self.send_error: BaseException | None = None
        self.send_gate: asyncio.Event | None = None
        self._held = 0
        self._held_event = asyncio.Event()
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self._sent_event = asyncio.Event()

    def feed(self, frame: dict[str, Any] | str) -> None:
        self._incoming.put_nowait(frame if isinstance(frame, str) else json.dumps(frame))

    async def wait_for_sent(self, n: int) -> None:
        """Resolve once ``n`` frames have been sent."""
        while len(self.sent) < n:
            self._sent_event.clear()
            await self._sent_event.wait()

    def sent_of_type(self, message_type: str) -> list[dict[str, Any]]:
        return [f for f in self.sent if f.get("_type") == message_type]

    async def wait_for_held_sends(self, n: int) -> None:
        """Resolve once ``n`` sends are waiting on ``send_gate``."""
        while self._held < n:
            self._held_event.clear()
            await self._held_event.wait()

    async def send(self, text: str) -> None:
        if (gate := self.send_gate) is not None and not gate.is_set():
            self._held += 1
            self._held_event.set()
            try:
                await gate.wait()
            finally:
                self._held -= 1
        if self.closed or self.fail_send:
            raise GatewayConnectError("socket closed")
        if (error := self.send_error) is not None:
            self.send_error = None
            raise error
        self.sent_raw.append(text)
        try:
            self.sent.append(json.loads(text))
        except ValueError as exc:
            raise FakeMisuseError(f"session sent a non-JSON frame: {text[:80]!r}") from exc
        self._sent_event.set()

    async def recv(self) -> str:
        if self.closed and self._incoming.empty():
            raise GatewayConnectError("socket closed by peer")
        frame = await self._incoming.get()
        if frame is None:
            raise GatewayConnectError("socket closed by peer")
        return frame

    async def close(self) -> None:
        self.closed = True
        self._incoming.put_nowait(None)

    @property
    def is_open(self) -> bool:
        return not self.closed


class FakeGatewayTransport:
    """Implements ``GatewayTransport``. Each ``connect`` pops the next scripted outcome."""

    def __init__(self, *outcomes: FakeGatewayConnection | BaseException) -> None:
        self.outcomes: deque[FakeGatewayConnection | BaseException] = deque(outcomes)
        self.urls: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    def queue(self, *outcomes: FakeGatewayConnection | BaseException) -> None:
        self.outcomes.extend(outcomes)

    async def connect(self, url: str, *, timeout_s: float, verify_ssl: bool) -> FakeGatewayConnection:
        self.urls.append(url)
        self.kwargs.append({"timeout_s": timeout_s, "verify_ssl": verify_ssl})
        if not self.outcomes:
            raise FakeMisuseError(f"no connection scripted for {url}")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _RecordedResponse:
    """The slice of ``aiohttp.ClientResponse`` the clients read: ``status`` and ``text(errors=)``, decoding UTF-8."""

    def __init__(self, status: int, body: bytes | str) -> None:
        self.status = status
        self._body = body.encode() if isinstance(body, str) else body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def text(self, errors: str = "strict") -> str:
        return self._body.decode("utf-8", errors)


class RecordingHttpSession:
    """Stands in for a borrowed ``aiohttp.ClientSession``: ``post`` records its url and kwargs, answers a script.

    Each scripted outcome is ``(status, body)``; ``calls`` holds ``(url, kwargs)`` in order, so a test can read
    what reached aiohttp (``ssl``, ``timeout``, ``headers``) below the clients' ``_post`` seam.
    """

    def __init__(self, *outcomes: tuple[int, bytes | str]) -> None:
        self.outcomes: deque[tuple[int, bytes | str]] = deque(outcomes)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _RecordedResponse:
        self.calls.append((url, kwargs))
        if not self.outcomes:
            raise FakeMisuseError(f"no response scripted for {url}")
        return _RecordedResponse(*self.outcomes.popleft())

    async def close(self) -> None:
        raise FakeMisuseError("a client closed a session it borrowed")
