"""The real ``WebsocketsTransport``'s open timeout, on a loopback listener so the timeout is the only way to fail."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyagorartc.exceptions import GatewayConnectError
from pyagorartc.session import WebsocketsTransport
from tests.integration._helpers import SESSION_TIMEOUT_S

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def silent_url() -> AsyncIterator[str]:
    """A ``ws://`` URL whose listener accepts the TCP connection and then says nothing."""

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    held: list[asyncio.StreamWriter] = []
    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}"
    for writer in held:
        writer.close()
    server.close()
    await server.wait_closed()


class TestConnectTimeout:
    async def test_an_open_that_outlasts_the_timeout_raises_gateway_connect_error_from_it(
        self, silent_url: str
    ) -> None:
        transport = WebsocketsTransport()

        with pytest.raises(GatewayConnectError) as caught:
            await asyncio.wait_for(transport.connect(silent_url, timeout_s=0.0, verify_ssl=True), SESSION_TIMEOUT_S)

        assert isinstance(caught.value.__cause__, TimeoutError)
