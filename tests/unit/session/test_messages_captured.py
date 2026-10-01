"""Every parser and builder in ``session/messages.py`` against the frames captured on 2026-10-01 (``gateway/real/``)."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import TYPE_CHECKING, Any

import pytest

from pyagorartc.models import RemoteStream, SessionOptions
from pyagorartc.session.messages import (
    build_join,
    build_leave,
    build_ping,
    build_subscribe,
    existing_streams_from_join,
    is_quit,
    lacks_payload_type,
    parse_frame,
    parse_join_result,
    parse_notification,
    parse_p2p_ok,
    parse_remote_stream,
    parse_rtp_capability_change,
    parse_user_event,
)
from tests._helpers import CREDENTIALS, load_fixture, load_json_fixture

if TYPE_CHECKING:
    from pyagorartc.session.messages import GatewayFrame

VIEWER_UID = 123456
GATEWAY_CNAME = "o/i14u9pJrxRKAsu"


def captured(name: str) -> GatewayFrame:
    frame = parse_frame(load_fixture(f"gateway/real/{name}"))
    assert frame is not None
    return frame


def captured_json(name: str) -> dict[str, Any]:
    return load_json_fixture(f"gateway/real/{name}")


def canonical(frame: object) -> str:
    return json.dumps(frame, sort_keys=True)


class TestCapturedJoinResult:
    def test_reads_the_viewer_uid_vid_rejoin_token_and_an_rtx_offer(self) -> None:
        result = parse_join_result(captured("join_ok_luba2.json"))

        assert (result.uid, result.vid, result.rejoin_token) == (VIEWER_UID, 987654, "rejoin-token-not-real")
        assert result.offers_rtx

    def test_has_no_cid_cname_or_existing_stream(self) -> None:
        result = parse_join_result(captured("join_ok_luba3_vision.json"))

        assert (result.cid, result.cname, result.existing_streams) == (None, None, [])

    def test_keeps_the_gateway_ortc_with_the_client_role_and_one_fingerprint(self) -> None:
        dtls = parse_join_result(captured("join_ok_luba2.json")).ortc["dtlsParameters"]

        assert isinstance(dtls, dict)
        assert dtls["role"] == "client"
        assert [fp["algorithm"] for fp in dtls["fingerprints"]] == ["sha-256"]

    @pytest.mark.parametrize("name", ["join_ok_luba2.json", "join_ok_luba3_vision.json"])
    def test_the_walk_finds_no_stream_anywhere_in_the_payload(self, name: str) -> None:
        assert existing_streams_from_join(captured(name).message) == []


class TestCapturedEvents:
    def test_on_add_video_stream_carries_ssrcs_pt_and_the_gateway_cname_but_no_codec(self) -> None:
        stream = parse_remote_stream(captured("on_add_video_stream.json").message)

        assert stream == RemoteStream(
            uid=1, ssrc=40000, rtx_ssrc=40001, codec=None, payload_type=49, cname=GATEWAY_CNAME
        )
        assert not lacks_payload_type(stream)

    def test_a_second_publisher_gets_the_next_ssrc_pair(self) -> None:
        stream = parse_remote_stream(captured("on_add_video_stream_second_publisher.json").message)

        assert stream is not None
        assert (stream.uid, stream.ssrc, stream.rtx_ssrc) == (1, 40002, 40003)

    def test_on_user_online_carries_only_the_uid(self) -> None:
        assert parse_user_event(captured("on_user_online.json").message) == (1, None)

    def test_the_repeat_join_quit_is_a_quit_with_code_2003(self) -> None:
        notification = parse_notification(captured("on_notification_quit_repeat_join.json").message)

        assert notification == ("quit", 2003, "ERR_REPEAT_JOIN")
        assert is_quit(notification)

    def test_on_p2p_ok_names_the_viewer_and_a_proxied_path(self) -> None:
        assert parse_p2p_ok(captured("on_p2p_ok.json").message) == (VIEWER_UID, True)

    def test_on_rtp_capability_change_lists_upper_case_codecs(self) -> None:
        caps = parse_rtp_capability_change(captured("on_rtp_capability_change.json").message)

        assert caps == (("H264", "VP8"), False, False)


class TestCapturedResponses:
    def test_the_ping_reply_is_a_success_without_a_message(self) -> None:
        frame = captured("ping_reply.json")

        assert (frame.ok, frame.message, "_message" in frame.raw) == (True, {}, False)

    def test_the_subscribe_ack_echoes_p2pid_and_the_viewer_uid(self) -> None:
        frame = captured("subscribe_ack.json")

        assert frame.ok
        assert dict(frame.message) == {"p2pid": 1, "uid": VIEWER_UID}


class TestBuildersMatchTheCapture:
    def test_subscribe_matches_the_captured_frame_exactly(self) -> None:
        recorded = captured_json("subscribe.json")

        frame = build_subscribe(RemoteStream(uid=1, ssrc=40000), codec="vp8", rtx=True, request_id=recorded["_id"])

        assert canonical(frame) == canonical(recorded)

    def test_ping_and_leave_match_the_captured_frames_exactly(self) -> None:
        ping, leave = captured_json("ping.json"), captured_json("leave.json")

        assert canonical(build_ping(ping["_id"])) == canonical(ping)
        assert canonical(build_leave(leave["_id"])) == canonical(leave)

    def test_join_built_from_the_captured_inputs_matches_the_captured_frame_exactly(self) -> None:
        recorded_frame = captured_json("join_v3.json")
        recorded = recorded_frame["_message"]

        frame = build_join(
            replace(CREDENTIALS, license="license-not-real"),
            recorded["ortc"],
            recorded["ap_response"],
            options=SessionOptions(),
            session_id=recorded["session_id"],
            process_id=recorded["process_id"],
            client_ts_ms=recorded["join_ts"],
            request_id=recorded_frame["_id"],
        )

        assert canonical(frame) == canonical(recorded_frame)
