from __future__ import annotations

import asyncio
import logging

import pytest

from pyagora.const import (
    EDGE_DOMAIN_SUFFIX,
)
from pyagora.exceptions import GatewayConnectError, JoinRejectedError, JoinTimeoutError, SdpError, SessionClosedError
from pyagora.models import CloseReason, IceCandidate, SessionOptions
from pyagora.sdp import extract_inline_candidates
from tests._helpers import CREDENTIALS, RENEWED_TOKEN, RTC_TOKEN, SECRET_VALUES, UID, load_json_fixture
from tests.unit.session._helpers import (
    OFFER,
    SESSION_ID,
    TIMEOUT,
    WALL_S,
    Recorder,
    all_done,
    ap_with_fingerprints,
    ap_without_gateway_block,
    candidate_ips,
    join_ok_with_role,
    join_ok_without_fingerprints,
    rig,
    srflx_candidate,
)


class TestJoin:
    async def test_answers_with_the_setup_that_mirrors_the_gateway_client_role(self) -> None:
        answer = await rig().join()

        assert "a=setup:active" in answer
        assert "a=ice-lite" in answer

    async def test_answers_passive_when_the_gateway_takes_the_server_role(self) -> None:
        answer = await rig().join(join_ok_with_role("server"))

        assert "a=setup:passive" in answer

    async def test_answer_carries_no_mid_header_extension(self) -> None:
        answer = await rig().join()

        assert "urn:ietf:params:rtp-hdrext:sdes:mid" not in answer

    async def test_join_frame_nests_the_user_attributes(self) -> None:
        r = rig()

        await r.join()

        attributes = r.conn.sent[0]["_message"]["attributes"]
        assert list(attributes) == ["userAttributes"]
        assert attributes["userAttributes"]["enablePreallocPC"] is True

    async def test_join_frame_carries_offer_and_added_candidates_once_each(self) -> None:
        r = rig()
        r.session.add_ice_candidate(srflx_candidate())
        r.session.add_ice_candidate("a=" + extract_inline_candidates(OFFER)[0].candidate)

        await r.join()

        assert candidate_ips(r.conn.sent[0]) == ["192.0.2.50", "198.51.100.20"]

    async def test_join_frame_carries_the_gateway_ap_block_and_wall_clock_timestamp(self) -> None:
        r = rig()

        await r.join()

        message = r.conn.sent[0]["_message"]
        assert (message["ap_response"]["flag"], message["join_ts"]) == (4096, int(WALL_S * 1000))
        assert message["channel_key"] == RTC_TOKEN

    async def test_connects_to_the_first_gateway_edge_with_verified_tls(self) -> None:
        options = SessionOptions(connect_timeout_s=4.0)
        r = rig(options)

        await r.join()

        assert r.transport.urls == [f"wss://203-0-113-10{EDGE_DOMAIN_SUFFIX}:4713"]
        assert r.transport.kwargs == [{"timeout_s": 4.0, "verify_ssl": True}]

    async def test_tries_the_next_edge_when_one_refuses(self) -> None:
        r = rig(refusals=1)

        await r.join()

        assert r.transport.urls[1] == f"wss://203-0-113-11{EDGE_DOMAIN_SUFFIX}:4713"
        assert r.session.is_joined

    async def test_reports_joined_and_connected_after_the_join(self) -> None:
        r = rig()

        await r.join()

        assert (r.session.is_joined, r.session.is_connected, r.session.close_reason) == (True, True, None)

    async def test_a_second_join_raises_session_closed(self) -> None:
        r = rig()
        await r.join()

        with pytest.raises(SessionClosedError):
            await r.session.join(OFFER, SESSION_ID)

    async def test_sends_a_prefixed_candidate_object_once_alongside_the_same_inline_candidate(self) -> None:
        r = rig()
        inline = extract_inline_candidates(OFFER)[0]
        r.session.add_ice_candidate(IceCandidate("a=" + inline.candidate, sdp_mid=inline.sdp_mid))

        await r.join()

        assert candidate_ips(r.conn.sent[0]) == ["192.0.2.50"]

    async def test_sends_no_set_client_role_by_default(self) -> None:
        r = rig()

        await r.join()

        assert r.conn.sent_of_type("set_client_role") == []

    async def test_sends_set_client_role_when_asked(self) -> None:
        r = rig(SessionOptions(send_set_client_role=True))

        await r.join()

        (frame,) = r.conn.sent_of_type("set_client_role")
        assert frame["_message"] == {"role": "host", "level": 0, "client_ts": int(WALL_S * 1000)}


class TestAnswerFingerprint:
    """D26: the AP's detail-19 fingerprint stands in only when the gateway ORTC carries none."""

    async def test_keeps_the_gateway_fingerprint_when_the_ortc_has_one(self) -> None:
        answer = await rig().join()

        assert "a=fingerprint:sha-256 BD:3E:08" in answer
        assert "FP_A" not in answer

    async def test_uses_the_connected_edges_ap_fingerprint_when_the_ortc_has_none(self) -> None:
        answer = await rig(refusals=1).join(join_ok_without_fingerprints())

        assert "a=fingerprint:sha-256 FP_B" in answer

    async def test_falls_back_to_the_first_gateway_edge_that_has_one(self) -> None:
        answer = await rig(ap=ap_with_fingerprints(";;FP_C")).join(join_ok_without_fingerprints())

        assert "a=fingerprint:sha-256 FP_C" in answer

    async def test_keeps_the_algorithm_the_ap_value_names(self) -> None:
        answer = await rig(ap=ap_with_fingerprints("sha-384 C1:D2")).join(join_ok_without_fingerprints())

        assert "a=fingerprint:sha-384 C1:D2" in answer

    async def test_fills_in_dtls_parameters_the_gateway_left_out_and_keeps_the_default_role(self) -> None:
        frame = join_ok_without_fingerprints()
        del frame["_message"]["ortc"]["dtlsParameters"]

        answer = await rig().join(frame)

        assert "a=fingerprint:sha-256 FP_A" in answer
        assert "a=setup:active" in answer

    async def test_raises_sdp_error_and_reports_join_failed_when_neither_has_one(self) -> None:
        r = rig(ap=ap_with_fingerprints(None))

        with pytest.raises(SdpError, match="fingerprint"):
            await r.join(join_ok_without_fingerprints())

        assert r.closed.calls == [CloseReason.JOIN_FAILED]


class TestJoinFailure:
    async def test_a_rejected_join_raises_and_reports_join_failed_once(self) -> None:
        r = rig()

        with pytest.raises(JoinRejectedError) as caught:
            await r.join("join_failed")

        assert caught.value.code == 2003
        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    async def test_a_rejected_join_sends_no_leave_and_leaves_nothing_running(self) -> None:
        r = rig()

        with pytest.raises(JoinRejectedError):
            await r.join("join_failed")

        assert r.conn.sent_of_type("leave") == []
        assert r.conn.closed
        assert all_done(r.spawned)

    async def test_a_success_without_ortc_is_a_rejection(self) -> None:
        r = rig()

        with pytest.raises(JoinRejectedError):
            await r.join("join_ok_no_ortc")

        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    async def test_no_join_result_within_the_timeout_raises_join_timeout(self) -> None:
        r = rig(SessionOptions(join_timeout_s=5.0))
        task = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
        await r.sent(1)
        await r.sleepers(1)

        r.sleep.advance(5.0)

        with pytest.raises(JoinTimeoutError):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.JOIN_FAILED]
        assert all_done(r.spawned)

    async def test_every_edge_refusing_raises_gateway_connect_error(self) -> None:
        r = rig(refusals=3)

        with pytest.raises(GatewayConnectError):
            await r.session.join(OFFER, SESSION_ID)

        assert len(r.transport.urls) == 3
        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    @pytest.mark.regression
    async def test_an_ap_without_gateway_edges_raises_without_dialling_anything(self) -> None:
        """With the gateway block failed, the session dialled the TURN block's edges as if they were gateways."""
        r = rig(ap=ap_without_gateway_block())

        with pytest.raises(GatewayConnectError):
            await asyncio.wait_for(r.session.join(OFFER, SESSION_ID), TIMEOUT)

        assert r.transport.urls == []
        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    @pytest.mark.regression
    async def test_a_join_the_socket_never_accepts_raises_gateway_connect_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The join send was unbounded: a socket that stalled on write hung ``join`` past its own timeout."""
        monkeypatch.setattr("pyagora.session.session.GATEWAY_SEND_TIMEOUT_S", 0.0)
        r = rig()
        r.conn.send_gate = asyncio.Event()

        with pytest.raises(GatewayConnectError):
            await asyncio.wait_for(r.session.join(OFFER, SESSION_ID), TIMEOUT)

        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    async def test_the_socket_closing_before_the_result_raises_gateway_connect_error(self) -> None:
        r = rig()
        task = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
        await r.sent(1)

        await r.conn.close()

        with pytest.raises(GatewayConnectError):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    @pytest.mark.regression
    async def test_the_socket_closing_right_after_the_result_fails_the_join(self) -> None:
        """A socket lost after the join result but before ``join`` resumed left the session joined, silently dead."""
        r = rig()
        task = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
        await r.sent(1)

        r.conn.feed({**load_json_fixture("gateway/join_ok.json"), "_id": r.conn.sent[0]["_id"]})
        await r.conn.close()

        with pytest.raises(GatewayConnectError):
            await asyncio.wait_for(task, TIMEOUT)
        assert r.closed.calls == [CloseReason.JOIN_FAILED]
        assert not r.session.is_joined

    async def test_an_unreadable_offer_raises_before_any_connection(self) -> None:
        r = rig()

        with pytest.raises(SdpError):
            await r.session.join("v=0\r\n", SESSION_ID)

        assert r.transport.urls == []
        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    async def test_close_after_a_failed_join_reports_nothing_more(self) -> None:
        r = rig()
        with pytest.raises(JoinRejectedError):
            await r.join("join_failed")

        await r.session.close()

        assert r.closed.calls == [CloseReason.JOIN_FAILED]


class TestSessionEndings:
    async def test_a_gateway_quit_ends_the_session_once_without_a_leave(self) -> None:
        r = rig()
        await r.join()

        r.conn.feed(load_json_fixture("gateway/on_notification_quit.json"))
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]
        assert r.conn.sent_of_type("leave") == []
        assert r.session.close_reason is CloseReason.GATEWAY_QUIT

    async def test_a_warning_notification_leaves_the_session_running(self) -> None:
        r = rig()
        await r.join()

        r.conn.feed(load_json_fixture("gateway/on_notification_warn.json"))
        await r.mark()

        assert r.closed.calls == []

    async def test_p2p_lost_is_ignored_by_default(self) -> None:
        r = rig()
        await r.join()

        r.conn.feed(load_json_fixture("gateway/on_p2p_lost.json"))
        await r.mark()

        assert (r.closed.calls, r.session.is_joined) == ([], True)

    async def test_p2p_lost_ends_the_session_when_asked(self) -> None:
        r = rig(SessionOptions(end_on_p2p_lost=True))
        await r.join()

        r.conn.feed(load_json_fixture("gateway/on_p2p_lost.json"))
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert r.closed.calls == [CloseReason.P2P_LOST]

    async def test_the_socket_closing_ends_the_session(self) -> None:
        r = rig()
        await r.join()

        await r.conn.close()
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        assert r.closed.calls == [CloseReason.SOCKET_CLOSED]
        assert not r.session.is_connected

    async def test_an_ending_by_the_gateway_leaves_nothing_running(self) -> None:
        r = rig()
        await r.join()
        r.conn.feed(load_json_fixture("gateway/on_notification_quit.json"))
        await asyncio.wait_for(r.closed.wait_for(1), TIMEOUT)

        await r.session.close()

        assert all_done(r.spawned)
        assert r.closed.calls == [CloseReason.GATEWAY_QUIT]

    async def test_close_from_inside_on_closed_does_not_deadlock(self) -> None:
        closed = Recorder()

        async def close_again(reason: CloseReason) -> None:
            await r.session.close()
            await closed(reason)

        r = rig(on_closed=close_again)
        await r.join()

        r.conn.feed(load_json_fixture("gateway/on_notification_quit.json"))

        await asyncio.wait_for(closed.wait_for(1), TIMEOUT)
        assert closed.calls == [CloseReason.GATEWAY_QUIT]


class TestClose:
    async def test_sends_leave_and_reports_closed_by_host_once(self) -> None:
        r = rig()
        await r.join()

        await r.session.close()
        await r.session.close()

        assert len(r.conn.sent_of_type("leave")) == 1
        assert r.closed.calls == [CloseReason.CLOSED_BY_HOST]

    async def test_leaves_no_task_running_and_the_socket_closed(self) -> None:
        r = rig(keepalive=Recorder(returns=[True]))
        await r.join()

        await r.session.close()

        assert all_done(r.spawned)
        assert r.conn.closed
        assert (r.session.is_connected, r.session.is_joined, r.session.remote_users) == (False, False, frozenset())

    async def test_closing_an_unjoined_session_sends_nothing(self) -> None:
        r = rig()

        await r.session.close()

        assert (r.conn.sent, r.closed.calls) == ([], [CloseReason.CLOSED_BY_HOST])

    async def test_a_join_after_close_raises_session_closed(self) -> None:
        r = rig()
        await r.session.close()

        with pytest.raises(SessionClosedError):
            await r.session.join(OFFER, SESSION_ID)

    async def test_a_raising_on_closed_does_not_propagate(self) -> None:
        r = rig(on_closed=Recorder(error=RuntimeError("host teardown failed")))
        await r.join()

        await r.session.close()

        assert r.session.close_reason is CloseReason.CLOSED_BY_HOST


class TestSpawn:
    async def test_every_background_task_comes_from_the_injected_factory(self) -> None:
        r = rig(keepalive=Recorder(returns=[True]))
        await r.join()
        await r.sent_type("subscribe")

        foreign = asyncio.all_tasks() - {asyncio.current_task()} - set(r.spawned)

        assert r.spawned
        assert foreign == set()


class TestCandidates:
    async def test_a_candidate_added_after_join_is_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join()

        r.session.add_ice_candidate(srflx_candidate())

        assert "198.51.100.20" not in candidate_ips(r.conn.sent[0])
        assert any("after join" in record.getMessage() for record in caplog.records)


class TestTolerance:
    async def test_logs_an_unknown_frame_type_once(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join()

        r.conn.feed(load_json_fixture("gateway/unknown_event.json"))
        r.conn.feed(load_json_fixture("gateway/unknown_event.json"))
        await r.mark()

        assert sum("on_published_user_list" in record.getMessage() for record in caplog.records) == 1

    async def test_survives_text_that_is_not_a_json_object(self) -> None:
        r = rig()
        await r.join()

        r.conn.feed("not json")
        r.conn.feed(load_json_fixture("gateway/non_object_message.json"))
        await r.mark()

        assert r.session.is_joined

    async def test_logs_a_gateway_error_event_and_carries_on(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join()

        r.conn.feed(load_json_fixture("gateway/error.json"))
        await r.mark()

        assert any(
            record.levelno == logging.WARNING and "some error string" in record.getMessage()
            for record in caplog.records
        )
        assert r.session.is_joined


class TestSecrets:
    async def test_repr_shows_channel_uid_and_state_only(self) -> None:
        r = rig()
        await r.join()

        text = repr(r.session)

        assert text == f"AgoraSession(channel={CREDENTIALS.channel_name!r}, uid={UID}, state=joined)"
        assert not [secret for secret in SECRET_VALUES if secret in text]

    async def test_no_secret_reaches_a_log_line_over_a_whole_session(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig(SessionOptions(send_set_client_role=True), token_provider=Recorder(returns=[RENEWED_TOKEN]))
        await r.join()
        r.conn.feed(load_json_fixture("gateway/on_token_privilege_will_expire.json"))
        await r.sent_type("renew_token")

        await r.session.close()

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert not [value for value in (*SECRET_VALUES, RENEWED_TOKEN) if value in logged]
