"""Every fault knob of the fake does what its name says, through ``control()`` and ``POST /control``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from websockets.exceptions import ConnectionClosedError

from tests.fakegateway import JoinRejection
from tests.integration._helpers import (
    AP_PATH,
    RECV_TIMEOUT_S,
    ap_envelope,
    ap_form,
    join_v3,
    recv_frame,
    request,
    rtm_body,
    rtm_headers,
    rtm_url,
    send,
    subscribe,
)

if TYPE_CHECKING:
    import aiohttp
    from websockets.asyncio.client import ClientConnection

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import Frame
    from tests.unit._fakes import ManualClock


async def _choose_server(raw_http: aiohttp.ClientSession, host: str) -> tuple[int, Frame | None]:
    async with raw_http.post(f"{host}{AP_PATH}", data=ap_form(ap_envelope())) as resp:
        return resp.status, await resp.json() if resp.status == 200 else None


async def _post_rtm(raw_http: aiohttp.ClientSession, host: str) -> tuple[int, Frame | None]:
    async with raw_http.post(rtm_url(host), json=rtm_body(), headers=rtm_headers()) as resp:
        return resp.status, await resp.json() if resp.status == 200 else None


class TestApKnobs:
    async def test_ap_fail_hosts_makes_the_first_hosts_answer_503(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(ap_fail_hosts=2)

        statuses = [(await _choose_server(raw_http, host))[0] for host in fake_agora.ap_hosts[:3]]

        assert statuses == [503, 503, 200]
        assert [r.host_index for r in fake_agora.state.log.ap_requests] == [0, 1, 2]

    async def test_ap_turn_code_fails_only_the_turn_block(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(ap_turn_code=7)

        _, body = await _choose_server(raw_http, fake_agora.ap_hosts[0])

        assert body is not None
        gateway, turn = (b["buffer"] for b in body["response_body"])
        assert (gateway["code"], turn["code"]) == (0, 7)
        assert turn["edges_services"] == []
        assert gateway["edges_services"]

    async def test_envelope_flag_order_puts_the_turn_block_first(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(envelope_flag_order=["turn", "gateway"])

        _, body = await _choose_server(raw_http, fake_agora.ap_hosts[0])

        assert body is not None
        assert [b["buffer"]["flag"] for b in body["response_body"]] == [4194310, 4096]


class TestRtmKnobs:
    async def test_rtm_fail_first_fails_the_first_posts_across_hosts(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(rtm_fail_first=2)

        statuses = [(await _post_rtm(raw_http, host))[0] for host in (*fake_agora.rtm_hosts, fake_agora.rtm_hosts[0])]

        assert statuses == [503, 503, 200]

    async def test_rtm_code_replaces_the_ack_code(self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession) -> None:
        fake_agora.control(rtm_code="message_offline")

        _, body = await _post_rtm(raw_http, fake_agora.rtm_hosts[0])

        assert body is not None
        assert (body["result"], body["code"]) == ("success", "message_offline")


class TestJoinKnobs:
    @pytest.mark.parametrize(
        "rejection",
        [JoinRejection(2031, "ERR_TOO_MANY_BROADCASTERS"), {"code": 2031, "message": "ERR_TOO_MANY_BROADCASTERS"}],
    )
    async def test_reject_join_answers_every_join_with_the_given_failure(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection, rejection: object
    ) -> None:
        fake_agora.control(reject_join=rejection)
        await send(raw_ws, join_v3("a1b2c3"))

        reply = await recv_frame(raw_ws)

        assert reply == {
            "_id": "a1b2c3",
            "_result": "failed",
            "_message": {"error_code": 2031, "error_str": "ERR_TOO_MANY_BROADCASTERS"},
        }

    async def test_drop_socket_after_join_aborts_right_after_the_success(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(drop_socket_after_join=True)
        await send(raw_ws, join_v3())

        reply = await recv_frame(raw_ws)

        assert reply["_result"] == "success"
        with pytest.raises(ConnectionClosedError):
            await recv_frame(raw_ws)

    async def test_peer_online_announces_the_device_right_after_the_join(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(peer_online=True)
        await send(raw_ws, join_v3())

        types = [(await recv_frame(raw_ws)).get("_type") for _ in range(3)]

        assert types == [None, "on_user_online", "on_add_video_stream"]

    async def test_dtls_role_sets_the_role_in_the_join_ortc(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(dtls_role="client")
        await send(raw_ws, join_v3())

        reply = await recv_frame(raw_ws)

        assert reply["_message"]["ortc"]["dtlsParameters"]["role"] == "client"


class TestTimeDrivenKnobs:
    @pytest.mark.parametrize(
        ("knob", "frame_type"), [("send_quit_after_s", "on_notification"), ("send_p2p_lost_after_s", "on_p2p_lost")]
    )
    async def test_fires_once_the_fake_clock_passes_the_delay(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection, knob: str, frame_type: str
    ) -> None:
        fake_agora.control(**{knob: 3.0})
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)

        await fake_agora.advance(2.9)
        early = fake_agora.state.log.sent_of_type(frame_type)
        await fake_agora.advance(0.1)
        frame = await recv_frame(raw_ws)

        assert early == []
        assert frame["_type"] == frame_type

    async def test_timers_are_cancelled_when_the_client_leaves(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(send_quit_after_s=3.0)
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)
        await send(raw_ws, request("leave"))
        await asyncio.wait_for(raw_ws.wait_closed(), timeout=RECV_TIMEOUT_S)

        await fake_agora.advance(5.0)

        assert fake_agora.scheduler.live_timers == 0
        assert fake_agora.state.log.sent_of_type("on_notification") == []

    async def test_token_will_expire_sends_one_notice_per_fake_second(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(token_will_expire=True)
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)

        await fake_agora.advance(3.5)

        assert len(fake_agora.state.log.sent_of_type("on_token_privilege_will_expire")) == 3

    async def test_token_will_expire_stops_when_switched_off(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(token_will_expire=True)
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)
        await fake_agora.advance(2.0)

        fake_agora.control(token_will_expire=False)
        await fake_agora.advance(5.0)

        assert len(fake_agora.state.log.sent_of_type("on_token_privilege_will_expire")) == 2

    async def test_advance_moves_the_injected_clock(self, fake_agora: FakeAgora, fake_clock: ManualClock) -> None:
        start = fake_clock()

        await fake_agora.advance(12.5)

        assert fake_clock() == start + 12.5


class TestControlSurface:
    def test_control_rejects_an_unknown_knob(self, fake_agora: FakeAgora) -> None:
        with pytest.raises(ValueError, match="warp_speed"):
            fake_agora.control(warp_speed=9)

    async def test_control_route_sets_knobs_and_reports_them(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        async with raw_http.post(
            fake_agora.control_url,
            json={"knobs": {"ap_fail_hosts": 1, "reject_join": {"code": 110, "message": "ERR_NO_AUTHORIZED"}}},
        ) as resp:
            status, body = resp.status, await resp.json()

        assert status == 200
        assert body["knobs"]["ap_fail_hosts"] == 1
        assert fake_agora.state.reject_join == JoinRejection(110, "ERR_NO_AUTHORIZED")

    async def test_control_route_rejects_an_unknown_knob(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        async with raw_http.post(fake_agora.control_url, json={"knobs": {"warp_speed": 9}}) as resp:
            status = resp.status

        assert status == 400

    async def test_control_route_runs_an_action_against_joined_sockets(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, joined_ws: ClientConnection
    ) -> None:
        async with raw_http.post(fake_agora.control_url, json={"action": "send_p2p_lost"}) as resp:
            status = resp.status

        frame = await recv_frame(joined_ws)

        assert status == 200
        assert frame["_type"] == "on_p2p_lost"

    async def test_control_route_advances_the_clock(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, fake_clock: ManualClock
    ) -> None:
        start = fake_clock()

        async with raw_http.post(fake_agora.control_url, json={"action": "advance", "args": {"seconds": 4}}) as resp:
            status = resp.status

        assert status == 200
        assert fake_clock() == start + 4

    async def test_control_route_rejects_an_unknown_action(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        async with raw_http.post(fake_agora.control_url, json={"action": "warp"}) as resp:
            status = resp.status

        assert status == 400

    async def test_control_route_drop_socket_aborts_joined_sockets(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, joined_ws: ClientConnection
    ) -> None:
        async with raw_http.post(fake_agora.control_url, json={"action": "drop_socket"}):
            pass

        with pytest.raises(ConnectionClosedError):
            await recv_frame(joined_ws)


class TestLogWaiting:
    async def test_wait_until_resolves_once_a_matching_frame_is_logged(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        log = fake_agora.state.log
        waiter = asyncio.create_task(log.wait_until(lambda: bool(log.received_of_type("subscribe"))))
        await asyncio.sleep(0)
        pending_before = not waiter.done()

        await send(joined_ws, subscribe())
        await asyncio.wait_for(waiter, timeout=RECV_TIMEOUT_S)

        assert pending_before
        assert log.received_of_type("subscribe") == [subscribe()]


class TestScheduler:
    @pytest.mark.regression
    async def test_a_cancelled_sleeper_is_neither_counted_nor_woken(self, fake_agora: FakeAgora) -> None:
        """A sleeper's future is cancelled a loop turn before its ``finally`` marks the timer; the scheduler counted
        it as pending and ``advance`` called ``set_result`` on it (``InvalidStateError``)."""
        scheduler = fake_agora.scheduler
        sleeper = asyncio.create_task(scheduler.sleep(1.0))
        await asyncio.wait_for(scheduler.wait_for_sleepers(1), timeout=RECV_TIMEOUT_S)

        sleeper.cancel()
        pending = scheduler.pending_sleepers
        await asyncio.wait_for(scheduler.advance(1.0), timeout=RECV_TIMEOUT_S)

        assert pending == 0
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(sleeper, timeout=RECV_TIMEOUT_S)
