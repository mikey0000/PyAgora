from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from sdp_transform import parse as sdp_parse

from pyagorartc.exceptions import SdpError
from pyagorartc.models import RemoteStream, SessionOptions
from pyagorartc.sdp.answer import STRIPPED_EXTENSIONS, answer_from_ortc, offers_rtx, setup_for_role, validate_answer
from pyagorartc.sdp.offer import offer_to_ortc
from tests.unit.sdp._helpers import (
    MID_URI,
    chrome_answer,
    chrome_offer,
    crlf,
    gateway_ortc,
    go2rtc_offer,
    section_lines,
    with_line_after,
    without_lines,
)

DEFAULTS = SessionOptions()
VIDEO = RemoteStream(uid=1, ssrc=11111, rtx_ssrc=22222, cname="mowercname")


def _with_video_rtx(ortc: dict[str, Any]) -> dict[str, Any]:
    ortc["rtpCapabilities"]["sendrecv"]["videoCodecs"].append(
        {
            "payloadType": 97,
            "rtpMap": {"encodingName": "rtx", "clockRate": 90000},
            "fmtp": {"parameters": {"apt": "96"}},
        }
    )
    return ortc


class TestShippedParity:
    def test_matches_the_shipped_answer_for_the_chrome_offer(self) -> None:
        shipped = chrome_answer()

        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=DEFAULTS)

        assert answer == shipped


class TestEndToEnd:
    def test_answers_the_chrome_offer_under_the_ids_its_ortc_declared(self) -> None:
        offer = chrome_offer()
        sent = json.loads(json.dumps(offer_to_ortc(offer)))
        declared = {
            kind: {(e["entry"], e["extensionName"]) for e in sent["rtpCapabilities"]["sendrecv"][f"{kind}Extensions"]}
            for kind in ("audio", "video")
        }
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["role"] = "server"

        answer = answer_from_ortc(gateway, offer, options=DEFAULTS)

        parsed = sdp_parse(answer)
        assert [(m["type"], str(m["mid"])) for m in parsed["media"]] == [("audio", "0"), ("video", "1")]
        assert [m["setup"] for m in parsed["media"]] == ["passive", "passive"]
        assert parsed["icelite"] == "ice-lite"
        answered = {m["type"]: {(e["value"], e["uri"]) for e in m["ext"]} for m in parsed["media"]}
        assert answered["audio"]
        assert answered["video"]
        assert all(answered[kind] <= declared[kind] for kind in ("audio", "video"))
        assert all(uri != MID_URI for extensions in answered.values() for _, uri in extensions)

    def test_answers_the_go2rtc_offer_in_its_own_mline_order(self) -> None:
        answer = answer_from_ortc(gateway_ortc("gateway_join_ortc"), go2rtc_offer(), options=DEFAULTS)

        parsed = sdp_parse(answer)
        assert [(m["type"], str(m["mid"]), m["payloads"]) for m in parsed["media"]] == [
            ("video", "0", 102),
            ("audio", "1", 111),
        ]
        assert [m["direction"] for m in parsed["media"]] == ["sendonly", "sendonly"]


class TestSetupForRole:
    @pytest.mark.parametrize(
        ("role", "setup"),
        [("server", "passive"), ("client", "active"), ("auto", "active"), (None, "active")],
    )
    def test_mirrors_the_gateway_dtls_role(self, role: str | None, setup: str) -> None:
        assert setup_for_role(role) == setup

    def test_rejects_a_role_it_does_not_know(self) -> None:
        with pytest.raises(SdpError, match="DTLS role"):
            setup_for_role("listener")

    @pytest.mark.parametrize(("role", "setup"), [("server", "passive"), ("client", "active"), ("auto", "active")])
    def test_answer_carries_the_setup_for_the_gateway_role_in_every_section(self, role: str, setup: str) -> None:
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["role"] = role

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert [m["setup"] for m in sdp_parse(answer)["media"]] == [setup, setup]

    @pytest.mark.regression
    def test_never_answers_actpass(self) -> None:
        """The builder answered a gateway ``auto`` role with ``actpass``, which RFC 5763 §5 forbids in an answer."""
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["role"] = "auto"

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=setup:actpass" not in answer

    def test_answer_is_active_when_the_gateway_reports_no_role(self) -> None:
        gateway = gateway_ortc()
        del gateway["dtlsParameters"]["role"]

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert [m["setup"] for m in sdp_parse(answer)["media"]] == ["active", "active"]


class TestMidExtension:
    def test_strips_the_mid_extension_by_default(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=DEFAULTS)

        assert MID_URI not in answer

    def test_keeps_the_mid_extension_with_the_offer_id_when_the_option_is_off(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=SessionOptions(strip_mid_extension=False))

        assert f"a=extmap:4 {MID_URI}" in section_lines(answer, "0")
        assert f"a=extmap:4 {MID_URI}" in section_lines(answer, "1")

    def test_keeps_every_other_shared_extension_when_stripping(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=DEFAULTS)

        assert frozenset({MID_URI}) == STRIPPED_EXTENSIONS
        assert [line for line in section_lines(answer, "1") if line.startswith("a=extmap:")] == [
            "a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
            "a=extmap:3 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
        ]


class TestExtensions:
    def test_uses_the_offer_id_not_the_gateway_entry(self) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoExtensions"][0]["entry"] = 20

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time" in section_lines(answer, "1")

    def test_drops_a_gateway_extension_the_offer_did_not_list(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), go2rtc_offer(), options=DEFAULTS)

        assert "abs-send-time" not in answer
        assert "ssrc-audio-level" not in answer

    def test_looks_extension_ids_up_in_the_matching_section(self) -> None:
        offer = go2rtc_offer().replace(
            "a=extmap:1 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01\r\na=recvonly\r\n"
            "a=candidate",
            "a=extmap:5 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01\r\na=recvonly\r\n"
            "a=candidate",
        )

        answer = answer_from_ortc(gateway_ortc(), offer, options=DEFAULTS)

        assert "a=extmap:5 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01" in section_lines(
            answer, "0"
        )


class TestCodecs:
    def test_copies_gateway_payload_types_unchanged(self) -> None:
        answer = answer_from_ortc(gateway_ortc("gateway_join_ortc"), chrome_offer(), options=DEFAULTS)

        assert section_lines(answer, "1")[0] == "m=video 9 UDP/TLS/RTP/SAVPF 102"
        assert "a=rtpmap:102 H264/90000" in section_lines(answer, "1")

    def test_writes_a_key_only_fmtp_parameter_bare(self) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["audioCodecs"][0]["fmtp"]["parameters"]["111/111"] = None

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=fmtp:111 minptime=10;useinbandfec=1;111/111" in section_lines(answer, "0")

    def test_falls_back_to_the_offer_payload_types_when_the_gateway_lists_no_codec(self) -> None:
        gateway = gateway_ortc("gateway_join_ortc")
        gateway["rtpCapabilities"]["sendrecv"]["audioCodecs"] = []

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        audio = section_lines(answer, "0")
        assert audio[0] == "m=audio 9 UDP/TLS/RTP/SAVPF 111 63 9 0 8 13 110 126"
        assert "a=rtpmap:111 opus/48000/2" in audio
        assert "a=fmtp:63 111/111" in audio

    def test_answers_only_payload_types_with_an_rtpmap_from_a_42_type_offer(self) -> None:
        """D25: of the Chrome video m-line's 42 payload types only 96, 97 and 49 carry an ``a=rtpmap``."""
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"] = []

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        video = section_lines(answer, "1")
        assert video[0] == "m=video 9 UDP/TLS/RTP/SAVPF 96 97 49"
        assert [line for line in video if line.startswith(("a=rtpmap:", "a=fmtp:"))] == [
            "a=rtpmap:96 VP8/90000",
            "a=rtpmap:97 rtx/90000",
            "a=rtpmap:49 H265/90000",
            "a=fmtp:97 apt=96",
            "a=fmtp:49 level-id=93;profile-id=1;tier-flag=0;tx-mode=SRST",
        ]

    def test_keeps_a_static_payload_type_that_has_no_rtpmap(self) -> None:
        offer = without_lines(chrome_offer(), "a=rtpmap:0 ")
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["audioCodecs"] = []

        answer = answer_from_ortc(gateway, offer, options=DEFAULTS)

        assert section_lines(answer, "0")[0] == "m=audio 9 UDP/TLS/RTP/SAVPF 111 63 9 0 8 13 110 126"

    def test_rejects_a_section_whose_offer_has_only_bare_dynamic_payload_types(self) -> None:
        offer = without_lines(
            without_lines(without_lines(chrome_offer(), "a=rtpmap:96 "), "a=rtpmap:97 "), "a=rtpmap:49 "
        )
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"] = []

        answer = answer_from_ortc(gateway, offer, options=DEFAULTS)

        assert section_lines(answer, "1")[0].startswith("m=video 0 UDP/TLS/RTP/SAVPF ")
        assert "a=group:BUNDLE 0" in answer.splitlines()

    def test_rejects_a_gateway_codec_without_a_payload_type(self) -> None:
        gateway = gateway_ortc()
        del gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"][0]["payloadType"]

        with pytest.raises(SdpError, match="payloadType"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_rejects_a_gateway_codec_without_an_encoding_name(self) -> None:
        gateway = gateway_ortc()
        del gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"][0]["rtpMap"]["encodingName"]

        with pytest.raises(SdpError, match="encodingName"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)


class TestDirection:
    def test_answers_sendonly_to_a_recvonly_offer(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=DEFAULTS)

        assert [m["direction"] for m in sdp_parse(answer)["media"]] == ["sendonly", "sendonly"]

    def test_marks_audio_inactive_when_audio_is_disabled(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=SessionOptions(disable_audio=True))

        assert [m["direction"] for m in sdp_parse(answer)["media"]] == ["inactive", "sendonly"]

    @pytest.mark.parametrize(
        ("offered", "answered"),
        [("a=sendrecv", "sendrecv"), ("a=sendonly", "recvonly"), ("a=inactive", "inactive"), (None, "sendrecv")],
    )
    def test_complements_the_offered_direction(self, offered: str | None, answered: str) -> None:
        offer = without_lines(chrome_offer(), "a=recvonly")
        if offered:
            offer = with_line_after(offer, "a=rtcp-rsize", offered)

        answer = answer_from_ortc(gateway_ortc(), offer, options=DEFAULTS)

        assert sdp_parse(answer)["media"][0]["direction"] == answered


class TestMalformedGatewayOrtc:
    """Constitution §7 / code_style: a malformed node raises ``SdpError`` naming the key, or is skipped if optional."""

    @pytest.mark.parametrize("value", [["not", "an", "object"], "text", None])
    def test_raises_naming_ice_ufrag_when_ice_parameters_is_not_an_object(self, value: object) -> None:
        gateway = gateway_ortc()
        gateway["iceParameters"] = value

        with pytest.raises(SdpError, match="iceUfrag"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_raises_naming_the_fingerprint_when_dtls_parameters_is_not_an_object(self) -> None:
        gateway = gateway_ortc()
        gateway["dtlsParameters"] = ["sha-256"]

        with pytest.raises(SdpError, match="fingerprint"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_raises_naming_the_fingerprint_when_fingerprints_is_not_a_list(self) -> None:
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["fingerprints"] = "BD:3E:08"

        with pytest.raises(SdpError, match="fingerprint"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_skips_a_fingerprint_that_is_not_an_object(self, caplog: pytest.LogCaptureFixture) -> None:
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["fingerprints"].insert(0, "junk")

        with caplog.at_level(logging.DEBUG, logger="pyagorartc.sdp"):
            answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=fingerprint:sha-256 BD:3E:08" in section_lines(answer, "0")
        assert [r.levelno for r in caplog.records if "fingerprint" in r.getMessage()] == [logging.DEBUG]

    def test_skips_a_candidate_that_is_not_an_object(self) -> None:
        gateway = gateway_ortc()
        gateway["iceParameters"]["candidates"].insert(0, "candidate:1 1 udp 1 198.51.100.1 1 typ host")

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert [line for line in section_lines(answer, "0") if line.startswith("a=candidate:")] == [
            "a=candidate:udpcandidate 1 udp 2103266323 198.51.100.13 4707 typ host"
        ]

    def test_writes_no_candidates_when_candidates_is_not_a_list(self) -> None:
        gateway = gateway_ortc()
        gateway["iceParameters"]["candidates"] = {"ip": "198.51.100.13"}

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=candidate:" not in answer

    def test_raises_naming_the_codec_list_when_it_is_not_a_list(self) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"] = {"payloadType": 96}

        with pytest.raises(SdpError, match="videoCodecs"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_raises_naming_the_codec_list_when_an_entry_is_not_an_object(self) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"].append(96)

        with pytest.raises(SdpError, match="videoCodecs"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    @pytest.mark.parametrize("value", [None, "VP8", ["VP8"]])
    def test_raises_naming_the_encoding_name_when_rtp_map_is_not_an_object(self, value: object) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"][0]["rtpMap"] = value

        with pytest.raises(SdpError, match="encodingName"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    @pytest.mark.parametrize("feedback", [{"parameter": "pli"}, "nack", None])
    def test_skips_a_feedback_without_a_type(self, feedback: object, caplog: pytest.LogCaptureFixture) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"][0]["rtcpFeedbacks"].insert(0, feedback)

        with caplog.at_level(logging.DEBUG, logger="pyagorartc.sdp"):
            answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert [line for line in section_lines(answer, "1") if line.startswith("a=rtcp-fb:")] == [
            "a=rtcp-fb:96 goog-remb",
            "a=rtcp-fb:96 transport-cc",
            "a=rtcp-fb:96 ccm fir",
            "a=rtcp-fb:96 nack",
            "a=rtcp-fb:96 nack pli",
        ]
        assert any(r.levelno == logging.DEBUG and "rtcpFeedback" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("fmtp", [None, "minptime=10", {"parameters": ["minptime=10"]}])
    def test_writes_no_fmtp_when_the_codec_fmtp_is_not_an_object(self, fmtp: object) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["audioCodecs"][0]["fmtp"] = fmtp

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert not any(line.startswith("a=fmtp:") for line in section_lines(answer, "0"))

    def test_skips_a_header_extension_that_is_not_an_object(self) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoExtensions"].insert(0, "abs-send-time")

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert [line for line in section_lines(answer, "1") if line.startswith("a=extmap:")] == [
            "a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
            "a=extmap:3 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
        ]

    def test_answers_the_offer_codecs_when_rtp_capabilities_is_not_an_object(self) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"] = ["sendrecv"]

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert section_lines(answer, "1")[0] == "m=video 9 UDP/TLS/RTP/SAVPF 96 97 49"


class TestRemoteVideo:
    def test_declares_no_ssrc_or_msid_without_a_remote_stream(self) -> None:
        """D17: no ``a=msid`` for the plain case (Q8)."""
        answer = answer_from_ortc(_with_video_rtx(gateway_ortc()), chrome_offer(), options=DEFAULTS)

        assert "a=ssrc" not in answer
        assert "a=msid:" not in answer

    def test_declares_the_remote_video_ssrc_in_the_video_section_only(self) -> None:
        answer = answer_from_ortc(_with_video_rtx(gateway_ortc()), chrome_offer(), options=DEFAULTS, remote_video=VIDEO)

        video = section_lines(answer, "1")
        assert video[-7:] == [
            "a=msid:agora agora-video",
            "a=ssrc:11111 cname:mowercname",
            "a=ssrc:11111 msid:agora agora-video",
            "a=ssrc:11111 mslabel:agora",
            "a=ssrc:11111 label:agora-video",
            "a=ssrc-group:FID 11111 22222",
            "a=ssrc:22222 cname:mowercname",
        ]
        assert not any(line.startswith(("a=ssrc", "a=msid")) for line in section_lines(answer, "0"))

    def test_declares_the_remote_video_ssrc_in_the_first_video_section_only(self) -> None:
        video = section_lines(chrome_offer(), "1")
        offer = chrome_offer().replace("a=group:BUNDLE 0 1", "a=group:BUNDLE 0 1 2")
        offer += crlf("\n".join(video).replace("a=mid:1", "a=mid:2"))

        answer = answer_from_ortc(gateway_ortc(), offer, options=DEFAULTS, remote_video=VIDEO)

        assert "a=ssrc:11111 cname:mowercname" in section_lines(answer, "1")
        assert not any(line.startswith(("a=ssrc", "a=msid")) for line in section_lines(answer, "2"))

    def test_omits_the_rtx_group_when_the_gateway_offers_no_rtx(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=DEFAULTS, remote_video=VIDEO)

        assert "a=ssrc:11111 cname:mowercname" in answer
        assert "ssrc-group" not in answer
        assert "22222" not in answer

    def test_omits_the_rtx_group_when_the_stream_has_no_rtx_ssrc(self) -> None:
        stream = RemoteStream(uid=1, ssrc=11111, cname="mowercname")

        answer = answer_from_ortc(
            _with_video_rtx(gateway_ortc()), chrome_offer(), options=DEFAULTS, remote_video=stream
        )

        assert "ssrc-group" not in answer

    def test_takes_the_gateway_cname_when_the_stream_has_none(self) -> None:
        stream = RemoteStream(uid=1, ssrc=11111)

        answer = answer_from_ortc(gateway_ortc(), chrome_offer(), options=DEFAULTS, remote_video=stream)

        assert "a=ssrc:11111 cname:o/i14u9pJrxRKAsu" in answer


class TestOffersRtx:
    def test_is_false_for_the_live_gateway_caps(self) -> None:
        assert offers_rtx(gateway_ortc("gateway_join_ortc")) is False

    def test_is_true_when_the_gateway_lists_an_rtx_video_codec(self) -> None:
        assert offers_rtx(_with_video_rtx(gateway_ortc())) is True

    @pytest.mark.parametrize("codec", [None, "rtx", {"rtpMap": None}, {"rtpMap": "rtx"}])
    def test_ignores_a_codec_entry_that_is_not_well_formed(self, codec: object) -> None:
        gateway = gateway_ortc()
        gateway["rtpCapabilities"]["sendrecv"]["videoCodecs"].insert(0, codec)

        assert offers_rtx(gateway) is False


class TestTransport:
    def test_writes_every_gateway_candidate_into_every_section(self) -> None:
        gateway = gateway_ortc()
        second = {**gateway["iceParameters"]["candidates"][0], "ip": "198.51.100.14", "generation": 0}
        gateway["iceParameters"]["candidates"].append(second)

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        for mid in ("0", "1"):
            assert [line for line in section_lines(answer, mid) if line.startswith("a=candidate:")] == [
                "a=candidate:udpcandidate 1 udp 2103266323 198.51.100.13 4707 typ host",
                "a=candidate:udpcandidate 1 udp 2103266323 198.51.100.14 4707 typ host generation 0",
            ]

    def test_skips_a_gateway_candidate_without_an_address(self) -> None:
        gateway = gateway_ortc()
        del gateway["iceParameters"]["candidates"][0]["ip"]

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=candidate:" not in answer

    def test_accepts_a_fingerprint_keyed_hash_function(self) -> None:
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["fingerprints"] = [{"hashFunction": "sha-384", "fingerprint": "BD:3E:08"}]

        answer = answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

        assert "a=fingerprint:sha-384 BD:3E:08" in section_lines(answer, "0")

    @pytest.mark.parametrize("key", ["iceUfrag", "icePwd"])
    def test_rejects_gateway_ortc_without_ice_credentials(self, key: str) -> None:
        """D9: no random stand-in credentials."""
        gateway = gateway_ortc()
        del gateway["iceParameters"][key]

        with pytest.raises(SdpError, match=key):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_rejects_gateway_ortc_without_a_fingerprint(self) -> None:
        """D9: no random stand-in fingerprint."""
        gateway = gateway_ortc()
        gateway["dtlsParameters"]["fingerprints"] = []

        with pytest.raises(SdpError, match="fingerprint"):
            answer_from_ortc(gateway, chrome_offer(), options=DEFAULTS)

    def test_rejects_an_offer_it_cannot_parse(self) -> None:
        with pytest.raises(SdpError, match="no media sections"):
            answer_from_ortc(gateway_ortc(), "", options=DEFAULTS)


class TestSessionHeader:
    def test_bundles_the_offer_mids_when_the_offer_has_no_group(self) -> None:
        answer = answer_from_ortc(gateway_ortc(), without_lines(chrome_offer(), "a=group:"), options=DEFAULTS)

        assert "a=group:BUNDLE 0 1" in answer.splitlines()

    def test_invents_no_mid_or_bundle_for_an_offer_without_mids(self) -> None:
        offer = without_lines(without_lines(chrome_offer(), "a=mid:"), "a=group:")

        answer = answer_from_ortc(gateway_ortc(), offer, options=DEFAULTS)

        assert not any(line.startswith(("a=mid:", "a=group:")) for line in answer.splitlines())
        assert len(sdp_parse(answer)["media"]) == 2

    def test_omits_extmap_allow_mixed_when_the_offer_did(self) -> None:
        answer = answer_from_ortc(
            gateway_ortc(), without_lines(chrome_offer(), "a=extmap-allow-mixed"), options=DEFAULTS
        )

        assert "a=extmap-allow-mixed" not in answer

    def test_rejects_a_section_that_is_neither_audio_nor_video(self) -> None:
        offer = chrome_offer().replace("a=group:BUNDLE 0 1", "a=group:BUNDLE 0 1 2")
        offer += (
            "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\na=mid:2\r\na=sctp-port:5000\r\n"
        )

        answer = answer_from_ortc(gateway_ortc(), offer, options=DEFAULTS)

        assert section_lines(answer, "2") == [
            "m=application 0 UDP/DTLS/SCTP webrtc-datachannel",
            "c=IN IP4 0.0.0.0",
            "a=mid:2",
        ]
        assert "a=group:BUNDLE 0 1" in answer.splitlines()


class TestValidateAnswer:
    def test_accepts_the_shipped_answer(self) -> None:
        validate_answer(chrome_answer(), min_media_sections=2)

    @pytest.mark.parametrize("prefix", ["v=", "o=", "s=", "t="])
    def test_rejects_an_answer_missing_a_session_line(self, prefix: str) -> None:
        answer = without_lines(chrome_answer(), prefix)

        with pytest.raises(SdpError, match=prefix):
            validate_answer(answer)

    def test_rejects_an_empty_answer(self) -> None:
        with pytest.raises(SdpError, match="empty"):
            validate_answer("  \r\n")

    def test_rejects_an_answer_with_too_few_media_sections(self) -> None:
        answer = chrome_answer()

        with pytest.raises(SdpError, match="2 media sections, expected at least 3"):
            validate_answer(answer, min_media_sections=3)

    def test_rejects_an_answer_without_media_sections(self) -> None:
        shipped = chrome_answer()
        answer = shipped[: shipped.index("m=")]

        with pytest.raises(SdpError, match="0 media sections"):
            validate_answer(answer)

    def test_rejects_a_media_line_without_payload_types(self) -> None:
        answer = chrome_answer().replace("m=audio 9 UDP/TLS/RTP/SAVPF 111", "m=audio 9 UDP/TLS/RTP/SAVPF")

        with pytest.raises(SdpError, match="payload"):
            validate_answer(answer)
