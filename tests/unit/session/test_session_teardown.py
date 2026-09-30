from __future__ import annotations

import asyncio

import pytest

from pyagorartc.exceptions import SessionClosedError
from pyagorartc.models import CloseReason, SessionOptions
from tests._helpers import load_json_fixture
from tests.unit.session._helpers import (
    OFFER,
    SESSION_ID,
    TIMEOUT,
    Recorder,
    all_done,
    reply,
    rig,
)


class TestNothingOutlivesTheSession:
    @pytest.mark.regression
    async def test_a_close_during_the_set_client_role_send_leaves_no_task_and_no_keepalive(self) -> None:
        """``join`` resumed after ``close()`` had cancelled everything and spawned the ping and keep-alive loops.

        The keep-alive then ticked the device after ``on_closed``, and neither loop was ever cancelled.
        """
        keepalive = Recorder(returns=[True])
        r = rig(SessionOptions(send_set_client_role=True), keepalive=keepalive)
        joining = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
        await r.sent(1)
        r.conn.send_gate = asyncio.Event()
        r.conn.feed(reply("join_ok", r.conn.sent[0]))
        await asyncio.wait_for(r.conn.wait_for_held_sends(1), TIMEOUT)
        closing = asyncio.ensure_future(r.session.close())
        await asyncio.wait_for(r.conn.wait_for_held_sends(2), TIMEOUT)

        r.conn.send_gate.set()

        (outcome,) = await asyncio.wait_for(asyncio.gather(joining, return_exceptions=True), TIMEOUT)
        await asyncio.wait_for(closing, TIMEOUT)
        assert isinstance(outcome, SessionClosedError)
        assert keepalive.calls == []
        assert all_done(r.spawned)
        assert r.closed.calls == [CloseReason.CLOSED_BY_HOST]


class TestMessageLoopEnd:
    @pytest.mark.regression
    async def test_a_handler_that_raises_ends_the_session_as_socket_closed(self) -> None:
        """A handler exception killed the message loop and left the session joined, deaf to every later frame."""
        r = rig()
        await r.join()
        await r.sent_type("subscribe")
        r.conn.send_error = RuntimeError("transport bug")

        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))

        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)
        assert r.closed.calls == [CloseReason.SOCKET_CLOSED]
        assert not r.session.is_joined


class TestBoundedSends:
    @pytest.mark.regression
    async def test_a_leave_the_socket_never_accepts_does_not_hold_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``close()`` awaited the ``leave`` send unbounded, so a stalled socket hung the host's teardown."""
        monkeypatch.setattr("pyagorartc.session.session.GATEWAY_SEND_TIMEOUT_S", 0.0)
        r = rig()
        await r.join()
        r.conn.send_gate = asyncio.Event()

        await asyncio.wait_for(r.session.close(), TIMEOUT)

        assert r.conn.closed
        assert r.closed.calls == [CloseReason.CLOSED_BY_HOST]
