"""Integration-tier fixtures: the fake Agora, its clock, and raw clients to poke it with."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from pyagorartc.ap import AgoraAPClient
from tests._helpers import CREDENTIALS
from tests.fakegateway import FakeAgora, FakeAgoraState
from tests.integration._helpers import (
    SESSION_TIMEOUT_S,
    gateway_client,
    join_v3,
    recv_frame,
    send,
    session_rig,
)
from tests.unit._fakes import ManualClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from websockets.asyncio.client import ClientConnection

    from pyagorartc.ap import APResponse
    from pyagorartc.models import SessionOptions
    from tests.integration._helpers import SessionRig


@pytest.fixture
def fake_clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def fake_agora(fake_clock: ManualClock) -> AsyncIterator[FakeAgora]:
    async with FakeAgora(FakeAgoraState(clock=fake_clock)) as agora:
        yield agora


@pytest.fixture
async def raw_ws(fake_agora: FakeAgora) -> AsyncIterator[ClientConnection]:
    async with gateway_client(fake_agora.gateway_url) as ws:
        yield ws


@pytest.fixture
async def joined_ws(raw_ws: ClientConnection) -> ClientConnection:
    """``raw_ws`` after a successful ``join_v3`` (its reply already consumed)."""
    await send(raw_ws, join_v3())
    reply = await recv_frame(raw_ws)
    assert reply["_result"] == "success"
    return raw_ws


@pytest.fixture
async def raw_http() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
async def ap(fake_agora: FakeAgora, raw_http: aiohttp.ClientSession) -> APResponse:
    """The fake's ``choose_server`` answer, fetched by the real ``AgoraAPClient``."""
    client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)
    return await asyncio.wait_for(client.choose_server(CREDENTIALS), SESSION_TIMEOUT_S)


@pytest.fixture
async def new_session(
    fake_agora: FakeAgora, fake_clock: ManualClock, ap: APResponse
) -> AsyncIterator[Callable[..., SessionRig]]:
    """Build ``SessionRig``s on the fake; each is closed at teardown, before the fake stops."""
    rigs: list[SessionRig] = []

    def build(options: SessionOptions | None = None, **hooks: Any) -> SessionRig:
        rigs.append(rig := session_rig(fake_agora, fake_clock, ap, options, **hooks))
        return rig

    yield build
    for rig in rigs:
        await asyncio.wait_for(rig.session.close(), SESSION_TIMEOUT_S)
