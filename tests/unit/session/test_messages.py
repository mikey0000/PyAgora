from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import pytest

from pyagorartc.const import SDK_VERSION
from pyagorartc.exceptions import JoinRejectedError
from pyagorartc.models import ChannelEncryption, RemoteStream, SessionOptions
from pyagorartc.sdp import offers_rtx
from pyagorartc.session.messages import (
    BROWSER_USER_AGENT,
    FrameType,
    JoinResult,
    build_join,
    build_leave,
    build_ping,
    build_renew_token,
    build_set_client_role,
    build_subscribe,
    build_unsubscribe,
    describe_frame,
    encode_frame,
    existing_streams_from_join,
    is_quit,
    lacks_payload_type,
    new_process_id,
    new_request_id,
    parse_error,
    parse_frame,
    parse_join_result,
    parse_notification,
    parse_p2p_lost,
    parse_remote_stream,
    parse_rtp_capability_change,
    parse_user_event,
)
from tests._helpers import CREDENTIALS, ENCRYPTION_SECRET, load_fixture, load_json_fixture

if TYPE_CHECKING:
    from pyagorartc.models import ChannelCredentials
    from pyagorartc.session.messages import GatewayFrame

STREAM = RemoteStream(uid=1, ssrc=44444444)


def canonical(frame: object) -> str:
    return json.dumps(frame, sort_keys=True)


def frame_fixture(name: str) -> GatewayFrame:
    frame = parse_frame(load_fixture(f"gateway/{name}"))
    assert frame is not None
    return frame


def join_frame(credentials: ChannelCredentials, options: SessionOptions | None = None) -> dict[str, Any]:
    inputs = load_json_fixture("gateway/join_v3_inputs.json")
    return build_join(
        credentials,
        inputs["ortc"],
        inputs["ap_response"],
        options=options or SessionOptions(),
        session_id=inputs["session_id"],
        process_id=inputs["process_id"],
        client_ts_ms=inputs["client_ts_ms"],
        request_id=inputs["request_id"],
    )


class TestBuildJoin:
    def test_matches_the_expected_join_v3_frame_exactly(self, credentials: ChannelCredentials) -> None:
        frame = join_frame(credentials)

        assert canonical(frame) == canonical(load_json_fixture("gateway/join_v3_expected.json"))

    def test_nests_the_flags_under_user_attributes(self, credentials: ChannelCredentials) -> None:
        attributes = join_frame(credentials)["_message"]["attributes"]

        assert list(attributes) == ["userAttributes"]
        assert attributes["userAttributes"]["enablePreallocPC"] is True

    def test_sends_the_sdk_version_and_browser_string(self, credentials: ChannelCredentials) -> None:
        message = join_frame(credentials)["_message"]

        assert message["sdk_version"] == SDK_VERSION
        assert message["browser"] == BROWSER_USER_AGENT

    def test_uses_the_client_codec_from_options(self, credentials: ChannelCredentials) -> None:
        message = join_frame(credentials, SessionOptions(client_codec="h264"))["_message"]

        assert message["codec"] == "h264"

    def test_sets_instant_video_from_options(self, credentials: ChannelCredentials) -> None:
        message = join_frame(credentials, SessionOptions(instant_video=True))["_message"]

        assert message["attributes"]["userAttributes"]["enableInstantVideo"] is True

    def test_merges_extra_join_attributes_last(self, credentials: ChannelCredentials) -> None:
        options = SessionOptions(extra_join_attributes={"enableXR": False, "enableNewThing": 3})

        user_attributes = join_frame(credentials, options)["_message"]["attributes"]["userAttributes"]

        assert user_attributes["enableXR"] is False
        assert user_attributes["enableNewThing"] == 3

    def test_extra_join_attributes_reach_the_frame_as_plain_json(self, credentials: ChannelCredentials) -> None:
        options = SessionOptions(extra_join_attributes={"enableNewThing": 3})

        frame = join_frame(credentials, options)

        assert json.loads(encode_frame(frame))["_message"]["attributes"]["userAttributes"]["enableNewThing"] == 3

    def test_omits_license_string_uid_and_encryption_when_the_credentials_lack_them(
        self, credentials: ChannelCredentials
    ) -> None:
        message = join_frame(credentials)["_message"]

        assert {"license", "string_uid", "aes_mode", "aes_secret", "aes_salt"}.isdisjoint(message)
        assert message["details"] == {}

    def test_sends_the_license_when_the_credentials_carry_one(self, credentials: ChannelCredentials) -> None:
        message = join_frame(dataclasses.replace(credentials, license="LICENSE-TEST"))["_message"]

        assert message["license"] == "LICENSE-TEST"

    def test_sends_the_string_uid_at_top_level_and_as_detail_6(self, credentials: ChannelCredentials) -> None:
        message = join_frame(dataclasses.replace(credentials, string_uid="viewer-test"))["_message"]

        assert message["string_uid"] == "viewer-test"
        assert message["details"] == {"6": "viewer-test"}

    @pytest.mark.parametrize("salt", [bytes(range(32)), None], ids=["with-salt", "without-salt"])
    def test_never_sends_aes_fields_and_warns_once(
        self, credentials: ChannelCredentials, salt: bytes | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """D20/Q17: the SDK sends the secret RSA-wrapped, so a raw ``aes_secret`` is never put on the wire."""
        encryption = ChannelEncryption(mode="aes-256-gcm2", secret=ENCRYPTION_SECRET, salt=salt)

        with caplog.at_level(logging.DEBUG, logger="pyagorartc.session"):
            frame = join_frame(dataclasses.replace(credentials, encryption=encryption))

        assert canonical(frame) == canonical(load_json_fixture("gateway/join_v3_expected.json"))
        assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
            "Channel encryption (aes-256-gcm2) is not supported; joining without aes_* fields (D20)"
        ]

    def test_logs_no_warning_without_encryption(
        self, credentials: ChannelCredentials, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="pyagorartc.session"):
            join_frame(credentials)

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestBuildSubscribe:
    def test_matches_the_expected_subscribe_frame_exactly(self) -> None:
        frame = build_subscribe(STREAM, codec="vp8", rtx=False, request_id="b2c3d4")

        assert canonical(frame) == canonical(load_json_fixture("gateway/subscribe_expected.json"))

    def test_carries_the_given_codec_and_rtx_flag(self) -> None:
        message = build_subscribe(STREAM, codec="h264", rtx=True, request_id="b2c3d4")["_message"]

        assert (message["codec"], message["rtx"]) == ("h264", True)


class TestBuildOtherRequests:
    def test_unsubscribe_matches_the_expected_frame_exactly(self) -> None:
        frame = build_unsubscribe(1, request_id="c3d4e5")

        assert canonical(frame) == canonical(load_json_fixture("gateway/unsubscribe_expected.json"))

    def test_set_client_role_matches_the_expected_frame_exactly(self) -> None:
        frame = build_set_client_role("host", 0, client_ts_ms=1790716796000, request_id="e5f6a7")

        assert canonical(frame) == canonical(load_json_fixture("gateway/set_client_role_expected.json"))

    def test_ping_matches_the_expected_frame_exactly(self) -> None:
        assert canonical(build_ping("f6a7b8")) == canonical(load_json_fixture("gateway/ping_expected.json"))

    def test_leave_matches_the_expected_frame_exactly(self) -> None:
        assert canonical(build_leave("b8c9d0")) == canonical(load_json_fixture("gateway/leave_expected.json"))

    def test_renew_token_matches_the_expected_frame_exactly(self, credentials: ChannelCredentials) -> None:
        frame = build_renew_token(credentials.token, request_id="a7b8c9")

        assert canonical(frame) == canonical(load_json_fixture("gateway/renew_token_expected.json"))


class TestIdsAndEncoding:
    def test_request_ids_are_six_hex_characters(self) -> None:
        request_id = new_request_id()

        assert re.fullmatch(r"[0-9a-f]{6}", request_id)

    def test_process_ids_follow_the_sdk_shape(self) -> None:
        parts = new_process_id().split("-")

        assert parts[0] == "process"
        assert [len(p) for p in parts[1:]] == [8, 4, 4, 4, 12]

    def test_encoded_frame_round_trips(self) -> None:
        frame = build_ping("f6a7b8")

        assert json.loads(encode_frame(frame)) == frame


class TestParseFrame:
    def test_reads_an_event_envelope(self) -> None:
        frame = frame_fixture("on_user_online.json")

        assert (frame.id, frame.type, frame.result) == (None, FrameType.ON_USER_ONLINE, None)
        assert frame.is_event
        assert frame.message == {"uid": 1}

    def test_reads_a_response_envelope(self) -> None:
        frame = frame_fixture("ping_back.json")

        assert (frame.id, frame.result) == ("f6a7b8", "success")
        assert frame.is_response and frame.ok
        assert not frame.is_event

    def test_a_failed_response_is_not_ok(self) -> None:
        frame = frame_fixture("join_failed.json")

        assert frame.is_response and not frame.ok

    def test_keeps_an_unknown_type_and_ignores_extra_keys(self) -> None:
        frame = frame_fixture("unknown_event.json")

        assert frame.type == "on_published_user_list"
        assert frame.message == {"users": [{"uid": 1}]}

    @pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"text"', "null"])
    def test_returns_none_for_text_that_is_not_a_json_object(self, text: str) -> None:
        assert parse_frame(text) is None

    def test_returns_none_for_text_nested_past_the_decoder_recursion_limit(self) -> None:
        text = "[" * 100_000 + "]" * 100_000

        assert parse_frame(text) is None

    def test_treats_a_non_object_message_as_empty(self) -> None:
        assert frame_fixture("non_object_message.json").message == {}

    def test_repr_names_the_envelope_and_not_the_payload(self) -> None:
        text = repr(frame_fixture("join_ok.json"))

        assert "a1b2c3" in text
        assert "rejoin-token-not-real" not in text


class TestDescribeFrame:
    def test_names_the_type_id_and_message_keys(self) -> None:
        text = describe_frame(frame_fixture("join_ok.json"))

        assert (
            text
            == "type=None id=a1b2c3 message_keys=['attributes', 'ortc', 'rejoin_token', 'return_vosip', 'uid', 'vid']"
        )

    def test_outlines_a_frame_about_to_be_sent_as_it_would_the_decoded_one(self) -> None:
        frame = build_ping("req-1")
        decoded = parse_frame(encode_frame(frame))

        assert decoded is not None
        assert describe_frame(frame) == describe_frame(decoded) == "type=ping id=req-1 message_keys=[]"

    def test_carries_no_payload_value(self) -> None:
        text = describe_frame(frame_fixture("join_ok.json"))

        assert "rejoin-token-not-real" not in text
        assert "ice-pwd-gateway-test-001" not in text


class TestParseJoinResult:
    def test_reads_the_session_identifiers_and_ortc(self) -> None:
        result = parse_join_result(frame_fixture("join_ok.json"))

        assert (result.uid, result.vid) == (123456, 987654)
        assert result.rejoin_token == "rejoin-token-not-real"
        assert result.ortc["iceParameters"]["iceUfrag"] == "KdDV"

    def test_reads_cid_and_cname_when_a_result_carries_them(self) -> None:
        result = parse_join_result(frame_fixture("join_ok_with_streams.json"))

        assert (result.cid, result.cname) == (123456789, "channel-test")

    def test_lists_the_video_streams_already_in_the_payload(self) -> None:
        result = parse_join_result(frame_fixture("join_ok_with_streams.json"))

        assert result.existing_streams == [
            RemoteStream(
                uid=1, ssrc=44444444, rtx_ssrc=44444445, codec="h264", payload_type=102, cname="o/i14u9pJrxRKAsu"
            )
        ]

    def test_reports_rtx_only_when_the_gateway_offered_it(self) -> None:
        assert not parse_join_result(frame_fixture("join_ok.json")).offers_rtx
        assert parse_join_result(frame_fixture("join_ok_rtx.json")).offers_rtx

    def test_tolerates_a_missing_rejoin_token(self) -> None:
        assert parse_join_result(frame_fixture("join_ok_rtx.json")).rejoin_token is None

    def test_raises_join_rejected_with_the_gateway_code_and_message(self) -> None:
        with pytest.raises(JoinRejectedError) as caught:
            parse_join_result(frame_fixture("join_failed.json"))

        assert (caught.value.code, caught.value.message) == (2003, "ERR_REPEAT_JOIN_CHANNEL")

    def test_falls_back_to_the_code_key_like_the_sdk(self) -> None:
        with pytest.raises(JoinRejectedError) as caught:
            parse_join_result(frame_fixture("join_failed_code_only.json"))

        assert caught.value.code == 118

    def test_a_failure_without_error_text_still_raises_with_its_code(self) -> None:
        with pytest.raises(JoinRejectedError) as caught:
            parse_join_result(frame_fixture("join_failed_code_only.json"))

        assert caught.value.message == "join failed"

    def test_raises_join_rejected_naming_the_missing_ortc(self) -> None:
        with pytest.raises(JoinRejectedError, match="ortc") as caught:
            parse_join_result(frame_fixture("join_ok_no_ortc.json"))

        assert caught.value.code is None

    def test_raises_join_rejected_for_a_frame_that_is_not_a_response(self) -> None:
        with pytest.raises(JoinRejectedError):
            parse_join_result(frame_fixture("on_user_online.json"))

    def test_repr_redacts_the_rejoin_token(self) -> None:
        result = parse_join_result(frame_fixture("join_ok.json"))

        assert isinstance(result, JoinResult)
        assert "rejoin-token-not-real" not in repr(result)


class TestOffersRtx:
    """``JoinResult.offers_rtx`` delegates to ``sdp.offers_rtx``: one bucket rule (``sdp.negotiated_caps``)."""

    @pytest.mark.parametrize(
        ("shape", "expected"),
        [("flat", True), ("recv_only", True), ("empty_sendrecv_then_recv", True), ("no_capabilities", False)],
    )
    def test_the_join_result_and_the_answer_builder_agree(self, shape: str, expected: bool) -> None:
        frame = frame_fixture("join_ok_rtx.json")
        codecs = frame.message["ortc"]["rtpCapabilities"]["sendrecv"]
        capabilities = {
            "flat": codecs,
            "recv_only": {"recv": codecs},
            "empty_sendrecv_then_recv": {"sendrecv": {}, "recv": codecs},
            "no_capabilities": None,
        }[shape]
        ortc = {**frame.message["ortc"], "rtpCapabilities": capabilities}
        result = parse_join_result(dataclasses.replace(frame, message={**frame.message, "ortc": ortc}))

        assert result.offers_rtx is offers_rtx(ortc)
        assert result.offers_rtx is expected

    @pytest.mark.regression
    def test_an_empty_sendrecv_bucket_does_not_hide_rtx_in_recv(self) -> None:
        """The session's own bucket walk took the first *present* bucket, so ``sendrecv: {}`` hid ``recv``'s RTX."""
        frame = frame_fixture("join_ok_rtx.json")
        codecs = frame.message["ortc"]["rtpCapabilities"]["sendrecv"]
        ortc = {**frame.message["ortc"], "rtpCapabilities": {"sendrecv": {}, "recv": codecs}}

        result = parse_join_result(dataclasses.replace(frame, message={**frame.message, "ortc": ortc}))

        assert result.offers_rtx


class TestExistingStreamsFromJoin:
    def test_dedupes_and_skips_audio_only_entries(self) -> None:
        message = frame_fixture("join_ok_with_streams.json").message
        doubled = {**message, "again": message["streams"]}

        assert [(s.uid, s.ssrc) for s in existing_streams_from_join(doubled)] == [(1, 44444444)]

    def test_finds_nothing_in_a_payload_without_streams(self) -> None:
        assert existing_streams_from_join(frame_fixture("join_ok_rtx.json").message) == []

    def test_a_type_video_key_alone_does_not_mark_a_stream(self) -> None:
        stream = dict(frame_fixture("join_ok_with_streams.json").message["streams"][1])
        stream["type"] = "video"

        assert existing_streams_from_join({"streams": [stream]}) == []

    def test_finds_a_stream_nested_within_the_search_depth(self) -> None:
        node: object = frame_fixture("join_ok_with_streams.json").message["streams"][0]
        for _ in range(31):
            node = {"inner": node}

        assert [(s.uid, s.ssrc) for s in existing_streams_from_join({"deep": node})] == [(1, 44444444)]

    def test_stops_searching_past_the_depth_bound(self) -> None:
        node: object = frame_fixture("join_ok_with_streams.json").message["streams"][0]
        for _ in range(32):
            node = {"inner": node}

        assert existing_streams_from_join({"deep": node}) == []


class TestParseRemoteStream:
    def test_reads_every_field_the_session_uses(self) -> None:
        stream = parse_remote_stream(frame_fixture("on_add_video_stream.json").message)

        assert stream == RemoteStream(
            uid=1, ssrc=44444444, rtx_ssrc=44444445, codec=None, payload_type=102, cname="o/i14u9pJrxRKAsu"
        )
        assert stream is not None and not lacks_payload_type(stream)

    def test_tolerates_missing_optional_fields(self) -> None:
        stream = parse_remote_stream(frame_fixture("on_add_video_stream_minimal.json").message)

        assert stream == RemoteStream(uid=1, ssrc=44444444)

    def test_returns_none_without_an_ssrc(self) -> None:
        assert parse_remote_stream(frame_fixture("on_add_video_stream_no_ssrc.json").message) is None

    def test_flags_and_warns_when_the_gateway_found_no_payload_type(self, caplog: pytest.LogCaptureFixture) -> None:
        stream = parse_remote_stream(frame_fixture("on_add_video_stream_h265_pt0.json").message)

        assert stream is not None and lacks_payload_type(stream)
        assert stream.codec == "h265"
        assert any(r.levelno == logging.WARNING and "pt=0" in r.getMessage() for r in caplog.records)


class TestParseEvents:
    def test_user_online_carries_the_uid(self) -> None:
        assert parse_user_event(frame_fixture("on_user_online.json").message) == (1, None)

    def test_user_offline_carries_the_uid_and_reason(self) -> None:
        assert parse_user_event(frame_fixture("on_user_offline.json").message) == (1, "quit")

    def test_user_event_without_a_uid_is_none(self) -> None:
        assert parse_user_event(frame_fixture("on_user_offline_no_uid.json").message) is None

    def test_quit_notification_is_a_quit(self) -> None:
        notification = parse_notification(frame_fixture("on_notification_quit.json").message)

        assert notification == ("quit", 2003, "ERR_REPEAT_JOIN")
        assert is_quit(notification)

    def test_warn_notification_is_not_a_quit(self) -> None:
        notification = parse_notification(frame_fixture("on_notification_warn.json").message)

        assert notification == ("warn", 1, None)
        assert not is_quit(notification)

    def test_p2p_lost_reads_its_fields_from_the_message(self) -> None:
        assert parse_p2p_lost(frame_fixture("on_p2p_lost.json")) == (1, "stun timeout")

    @pytest.mark.regression
    def test_p2p_lost_prefers_the_message_over_the_top_level(self) -> None:
        """HA-Luba's handler read ``error_code``/``error_str`` from the frame's top level, not from ``_message``."""
        assert parse_p2p_lost(frame_fixture("on_p2p_lost_top_level.json")) == (1, "stun timeout")

    def test_p2p_lost_falls_back_to_the_top_level_when_the_message_lacks_the_fields(self) -> None:
        assert parse_p2p_lost(frame_fixture("on_p2p_lost_top_level_only.json")) == (7, "top-level copy")

    def test_rtp_capability_change_drops_irregular_values(self) -> None:
        caps = parse_rtp_capability_change(frame_fixture("on_rtp_capability_change_irregular.json").message)

        assert caps == (("vp8",), False, False)

    def test_error_event_carries_its_error_string(self) -> None:
        assert parse_error(frame_fixture("error.json")) == (None, "some error string")

    def test_failed_response_error_carries_code_and_string(self) -> None:
        assert parse_error(frame_fixture("join_failed.json")) == (2003, "ERR_REPEAT_JOIN_CHANNEL")

    @pytest.mark.parametrize(
        ("name", "frame_type"),
        [
            ("on_token_privilege_will_expire.json", FrameType.ON_TOKEN_PRIVILEGE_WILL_EXPIRE),
            ("on_token_privilege_did_expire.json", FrameType.ON_TOKEN_PRIVILEGE_DID_EXPIRE),
            ("on_add_video_stream.json", FrameType.ON_ADD_VIDEO_STREAM),
            ("on_user_offline.json", FrameType.ON_USER_OFFLINE),
            ("on_notification_quit.json", FrameType.ON_NOTIFICATION),
            ("on_p2p_lost.json", FrameType.ON_P2P_LOST),
            ("on_p2p_ok.json", FrameType.ON_P2P_OK),
            ("on_rtp_capability_change.json", FrameType.ON_RTP_CAPABILITY_CHANGE),
            ("error.json", FrameType.ERROR),
        ],
    )
    def test_every_event_fixture_decodes_to_its_frame_type(self, name: str, frame_type: FrameType) -> None:
        assert frame_fixture(name).type == frame_type


class TestPreallocOption:
    def test_prealloc_pc_option_controls_the_flag(self) -> None:
        message = build_join(
            CREDENTIALS,
            {},
            {},
            options=SessionOptions(prealloc_pc=False),
            session_id="s",
            process_id="p",
            client_ts_ms=0,
            request_id="000001",
        )["_message"]

        assert message["attributes"]["userAttributes"]["enablePreallocPC"] is False
