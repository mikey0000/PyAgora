"""The hand-written doubles in ``tests/unit/_fakes.py`` keep the promises their docstrings make."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from pyagorartc.exceptions import GatewayConnectError
from tests.unit._fakes import FakeGatewayConnection

TIMEOUT = 1.0


async def _read_forever(conn: FakeGatewayConnection, seen: list[str]) -> None:
    with contextlib.suppress(GatewayConnectError):
        while True:
            seen.append(await conn.recv())


class TestWaitUntilRead:
    async def test_resolves_only_after_the_reader_took_every_fed_frame(self) -> None:
        conn, seen = FakeGatewayConnection(), []
        conn.feed({"n": 1})
        conn.feed({"n": 2})
        reader = asyncio.ensure_future(_read_forever(conn, seen))

        await asyncio.wait_for(conn.wait_until_read(), TIMEOUT)

        assert seen == ['{"n": 1}', '{"n": 2}']
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)

    async def test_waits_while_a_frame_is_queued_and_no_reader_runs(self) -> None:
        conn = FakeGatewayConnection()
        conn.feed({"n": 1})

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(conn.wait_until_read(), 0.01)

    @pytest.mark.regression
    async def test_resolves_when_a_parked_reader_stops_on_close(self) -> None:
        """Closing a connection whose reader was parked in ``recv`` made ``wait_until_read`` spin without yielding."""
        conn, seen = FakeGatewayConnection(), []
        reader = asyncio.ensure_future(_read_forever(conn, seen))
        await asyncio.wait_for(conn.wait_until_read(), TIMEOUT)

        await conn.close()
        await asyncio.wait_for(conn.wait_until_read(), TIMEOUT)

        await asyncio.wait_for(reader, TIMEOUT)
