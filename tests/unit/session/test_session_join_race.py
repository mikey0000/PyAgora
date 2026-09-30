"""An ending the gateway announces between the join result and ``join`` resuming (D23, D29)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyagorartc.exceptions import GatewayConnectError
from pyagorartc.models import CloseReason, SessionOptions
from tests._helpers import load_json_fixture
from tests.unit.session._helpers import OFFER, SESSION_ID, TIMEOUT, all_done, rig

if TYPE_CHECKING:
    from tests.unit.session._helpers import Rig


async def join_then(r: Rig, fixture: str) -> asyncio.Task[str]:
    """Start ``join`` and feed the result and ``fixture`` together, so the reader dispatches both before it resumes."""
    task = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
    await r.sent(1)
    r.conn.feed({**load_json_fixture("gateway/join_ok.json"), "_id": r.conn.sent[0]["_id"]})
    r.conn.feed(load_json_fixture(fixture))
    return task


class TestEndingDuringJoin:
    @pytest.mark.regression
    async def test_a_quit_right_after_the_result_fails_the_join_as_a_gateway_quit(self) -> None:
        """A 2003 quit dispatched before ``join`` resumed was dropped, so ``join`` answered for a quit session."""
        r = rig()
        task = await join_then(r, "gateway/on_notification_quit.json")

        with pytest.raises(GatewayConnectError, match="quit"):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]
        assert not r.session.is_joined
        assert all_done(r.spawned)
        assert r.conn.sent_of_type("leave") == []

    async def test_a_quit_then_a_socket_close_during_join_reports_the_quit(self) -> None:
        """The gateway closes the socket after quitting; the quit, not the close, is why the join failed."""
        r = rig()
        task = await join_then(r, "gateway/on_notification_quit.json")
        await r.conn.close()

        with pytest.raises(GatewayConnectError, match="quit"):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]

    async def test_a_quit_during_join_is_reported_once_after_close(self) -> None:
        r = rig()
        task = await join_then(r, "gateway/on_notification_quit.json")
        with pytest.raises(GatewayConnectError):
            await asyncio.wait_for(task, TIMEOUT)

        await r.session.close()

        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]

    @pytest.mark.regression
    async def test_p2p_lost_right_after_the_result_fails_the_join_when_asked(self) -> None:
        """With ``end_on_p2p_lost``, a p2p_lost dispatched before ``join`` resumed was dropped and the join succeeded."""
        r = rig(SessionOptions(end_on_p2p_lost=True))
        task = await join_then(r, "gateway/on_p2p_lost.json")

        with pytest.raises(GatewayConnectError):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.P2P_LOST]
        assert not r.session.is_joined
        assert all_done(r.spawned)
        assert r.conn.sent_of_type("leave") == []

    async def test_p2p_lost_right_after_the_result_is_ignored_by_default(self) -> None:
        r = rig()
        task = await join_then(r, "gateway/on_p2p_lost.json")

        answer = await asyncio.wait_for(task, TIMEOUT)

        assert "a=setup:" in answer
        assert (r.closed.calls, r.session.is_joined) == ([], True)

    async def test_a_quit_before_the_result_fails_the_join_without_waiting_for_the_timeout(self) -> None:
        """The quit fails the pending join at once rather than leaving it to ``join_timeout_s``."""
        r = rig()
        task = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
        await r.sent(1)

        r.conn.feed(load_json_fixture("gateway/on_notification_quit.json"))

        with pytest.raises(GatewayConnectError, match="quit"):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]
        assert all_done(r.spawned)
