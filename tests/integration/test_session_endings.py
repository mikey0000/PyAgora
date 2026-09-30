"""How a session against the fake Agora ends: the gateway's quit, a lost socket, p2p_lost, and the host's close."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pyagorartc.exceptions import GatewayConnectError
from pyagorartc.models import CloseReason, SessionOptions
from tests.unit._fakes import Recorder

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import SessionRig


class TestGatewayEndings:
    async def test_a_gateway_quit_ends_the_session_once_without_a_leave(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        await fake_agora.send_quit()
        await r.ended()
        await r.session.close()

        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]
        assert fake_agora.state.log.received_of_type("leave") == []

    async def test_a_quit_armed_on_fake_time_ends_the_session_when_it_falls_due(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(send_quit_after_s=10.0)
        r = new_session()
        await r.join()

        await r.advance(10.0)
        await r.ended()

        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]

    async def test_the_socket_dropping_right_after_the_join_result_ends_the_session_once(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        """The knob aborts the socket straight after the result, racing the client's read: the join either fails or
        succeeds and the loss ends it, but the session is never left joined on a dead socket. Each branch is pinned
        on its own: ``tests/unit/session/test_session.py::TestJoinFailure::
        test_the_socket_closing_right_after_the_result_fails_the_join`` and ``test_a_socket_dropped_mid_session_ends_it``.
        """
        fake_agora.control(drop_socket_after_join=True)
        r = new_session()

        (outcome,) = await asyncio.gather(r.join(), return_exceptions=True)
        await r.ended()

        assert isinstance(outcome, (str, GatewayConnectError))
        assert len(r.closed.calls) == 1
        assert (r.session.is_joined, r.session.is_connected) == (False, False)

    async def test_a_socket_dropped_mid_session_ends_it(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        fake_agora.drop_socket()
        await r.ended()

        assert r.closed.calls == [CloseReason.SOCKET_CLOSED]

    async def test_p2p_lost_is_ignored_by_default(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        await fake_agora.send_p2p_lost()
        await fake_agora.send_quit()
        await r.ended()

        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]  # the quit behind it is what ended the session

    async def test_p2p_lost_ends_the_session_when_asked(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session(SessionOptions(end_on_p2p_lost=True))
        await r.join()

        await fake_agora.send_p2p_lost()
        await r.ended()

        assert r.closed.calls == [CloseReason.P2P_LOST]


class TestClose:
    async def test_sends_leave_closes_the_socket_and_reports_closed_by_host_once(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        await r.session.close()
        await r.session.close()

        assert len(await r.received("leave")) == 1
        assert r.closed.calls == [CloseReason.CLOSED_BY_HOST]
        assert not r.session.is_connected

    async def test_leaves_no_task_running(self, new_session: Callable[..., SessionRig]) -> None:
        r = new_session(keepalive=Recorder(returns=[True]))
        await r.join()
        await r.received("subscribe")

        await r.session.close()

        assert r.spawned
        assert all(task.done() for task in r.spawned)
