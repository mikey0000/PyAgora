from __future__ import annotations

import json
from typing import Any

import pytest

from pyagora.exceptions import SdpError
from pyagora.sdp.offer import can_send, negotiated_caps, offer_to_ortc, parse_offer
from tests._helpers import load_json_fixture
from tests.unit.sdp._helpers import (
    MID_URI,
    chrome_offer,
    gateway_ortc,
    go2rtc_offer,
    with_line_after,
    without_lines,
)


def _codec(ortc: dict[str, Any], bucket: str, kind: str, payload_type: int) -> dict[str, Any]:
    return next(c for c in ortc["rtpCapabilities"][bucket][kind] if c["payloadType"] == payload_type)


def _payload_types(ortc: dict[str, Any], bucket: str, kind: str) -> list[int]:
    return [c["payloadType"] for c in ortc["rtpCapabilities"][bucket][kind]]


class TestShippedParity:
    def test_matches_the_shipped_ortc_for_the_chrome_offer(self) -> None:
        shipped = load_json_fixture("sdp/chrome_offer_ortc_shipped.json")

        ortc = offer_to_ortc(chrome_offer())

        assert ortc == shipped

    def test_is_json_serialisable_exactly_as_the_join_sends_it(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert json.loads(json.dumps(ortc)) == ortc

    def test_omits_the_dtls_role_when_none_is_requested(self) -> None:
        shipped = load_json_fixture("sdp/chrome_offer_ortc_shipped.json")
        del shipped["dtlsParameters"]["role"]

        ortc = offer_to_ortc(chrome_offer(), dtls_role=None)

        assert ortc == shipped

    def test_declares_the_requested_dtls_role(self) -> None:
        ortc = offer_to_ortc(chrome_offer(), dtls_role="client")

        assert ortc["dtlsParameters"]["role"] == "client"


class TestCanSend:
    @pytest.mark.parametrize(
        ("name", "parameters", "sendable"),
        [
            ("H265", {}, False),
            ("h265", {"profile-id": "1"}, False),
            ("VP9", {"profile-id": "1"}, False),
            ("VP9", {"profile-id": "3"}, False),
            ("VP9", {"profile-id": "0"}, True),
            ("VP9", {"profile-id": "2"}, True),
            ("VP9", {}, True),
            ("AV1", {"profile": "1"}, False),
            ("AV1", {"profile": "0"}, True),
            ("VP8", {}, True),
            ("H264", {"profile-level-id": "42e01f"}, True),
            ("opus", {"minptime": "10"}, True),
        ],
    )
    def test_matches_the_sdk_rule(self, name: str, parameters: dict[str, str], sendable: bool) -> None:
        codec = {
            "payloadType": 100,
            "rtpMap": {"encodingName": name, "clockRate": 90000},
            "fmtp": {"parameters": parameters},
        }

        assert can_send(codec) is sendable

    def test_treats_a_codec_without_fmtp_as_sendable(self) -> None:
        codec = {"payloadType": 96, "rtpMap": {"encodingName": "VP8", "clockRate": 90000}}

        assert can_send(codec) is True


class TestCodecBuckets:
    def test_routes_a_codec_the_browser_cannot_send_to_recv(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert _payload_types(ortc, "recv", "videoCodecs") == [49]
        assert 49 not in _payload_types(ortc, "sendrecv", "videoCodecs")

    def test_puts_sendable_codecs_in_sendrecv_whatever_the_mline_direction(self) -> None:
        ortc = offer_to_ortc(go2rtc_offer())

        assert _payload_types(ortc, "sendrecv", "videoCodecs") == [102, 103]
        assert _payload_types(ortc, "sendrecv", "audioCodecs") == [111, 0, 8]

    def test_leaves_the_send_bucket_empty(self) -> None:
        ortc = offer_to_ortc(go2rtc_offer())

        assert ortc["rtpCapabilities"]["send"] == {
            "audioCodecs": [],
            "audioExtensions": [],
            "videoCodecs": [],
            "videoExtensions": [],
        }

    def test_puts_every_extension_in_sendrecv(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert [e["entry"] for e in ortc["rtpCapabilities"]["sendrecv"]["audioExtensions"]] == [1, 2, 3, 4]
        assert ortc["rtpCapabilities"]["recv"]["videoExtensions"] == []

    def test_ignores_sections_that_are_neither_audio_nor_video(self) -> None:
        offer = chrome_offer() + "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\na=mid:2\r\n"

        ortc = offer_to_ortc(offer)

        assert ortc == load_json_fixture("sdp/chrome_offer_ortc_shipped.json")


class TestFeedback:
    def test_appends_rrtr_to_every_codec_that_lacks_one(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        codecs = [
            codec
            for bucket in ortc["rtpCapabilities"].values()
            for kind in ("audioCodecs", "videoCodecs")
            for codec in bucket[kind]
        ]
        assert codecs
        assert all(codec["rtcpFeedbacks"][-1] == {"type": "rrtr"} for codec in codecs)

    def test_does_not_duplicate_an_offered_rrtr(self) -> None:
        offer = with_line_after(chrome_offer(), "a=rtpmap:96 VP8/90000", "a=rtcp-fb:96 rrtr")

        ortc = offer_to_ortc(offer)

        feedback = _codec(ortc, "sendrecv", "videoCodecs", 96)["rtcpFeedbacks"]
        assert feedback.count({"type": "rrtr"}) == 1

    def test_keeps_offered_feedback_with_its_parameter_in_offer_order(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert _codec(ortc, "sendrecv", "videoCodecs", 96)["rtcpFeedbacks"] == [
            {"type": "goog-remb"},
            {"type": "transport-cc"},
            {"type": "ccm", "parameter": "fir"},
            {"type": "nack"},
            {"type": "nack", "parameter": "pli"},
            {"type": "rrtr"},
        ]

    def test_applies_wildcard_feedback_to_every_codec_in_its_section(self) -> None:
        """The shipped hand parser crashed on ``*`` (``int("*")``); RFC 4585 §4.2 applies it to every payload."""
        offer = with_line_after(go2rtc_offer(), "a=rtpmap:103 rtx/90000", "a=rtcp-fb:* transport-cc")

        ortc = offer_to_ortc(offer)

        assert {"type": "transport-cc"} in _codec(ortc, "sendrecv", "videoCodecs", 103)["rtcpFeedbacks"]
        assert {"type": "transport-cc"} not in _codec(ortc, "sendrecv", "audioCodecs", 0)["rtcpFeedbacks"]


class TestFmtp:
    def test_keeps_a_key_only_parameter_as_none(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert _codec(ortc, "sendrecv", "audioCodecs", 63)["fmtp"] == {"parameters": {"111/111": None}}

    def test_splits_key_value_parameters(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert _codec(ortc, "recv", "videoCodecs", 49)["fmtp"]["parameters"] == {
            "level-id": "93",
            "profile-id": "1",
            "tier-flag": "0",
            "tx-mode": "SRST",
        }

    def test_gives_a_codec_without_fmtp_empty_parameters(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert _codec(ortc, "sendrecv", "videoCodecs", 96)["fmtp"] == {"parameters": {}}


class TestExtmapDirection:
    def test_sdp_transform_parses_the_rfc_8285_direction_suffix(self) -> None:
        """Q1: the old hand parser needed a special case; the one parser (D3) handles it natively."""
        parsed = parse_offer(chrome_offer())

        video_ext = parsed["media"][1]["ext"]
        assert {
            "value": 12,
            "direction": "recvonly",
            "uri": "http://www.webrtc.org/experiments/rtp-hdrext/abs-capture-time",
        } in video_ext

    def test_reports_an_extension_with_a_direction_suffix_by_id_and_uri_only(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert ortc["rtpCapabilities"]["sendrecv"]["videoExtensions"][-1] == {
            "entry": 12,
            "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/abs-capture-time",
        }

    def test_keeps_the_mid_extension_in_the_ortc(self) -> None:
        """D16: MID is stripped from the answer only; Q7 asks whether the ORTC should lose it too."""
        ortc = offer_to_ortc(chrome_offer())

        assert {"entry": 4, "extensionName": MID_URI} in ortc["rtpCapabilities"]["sendrecv"]["videoExtensions"]


class TestIceAndDtls:
    def test_reads_media_level_ice_credentials_when_the_session_has_none(self) -> None:
        ortc = offer_to_ortc(go2rtc_offer())

        assert ortc["iceParameters"] == {"iceUfrag": "PionUfragTest", "icePwd": "not-a-real-ice-password"}

    def test_reads_a_session_level_fingerprint(self) -> None:
        ortc = offer_to_ortc(go2rtc_offer())

        assert ortc["dtlsParameters"]["fingerprints"] == [
            {"hashFunction": "sha-256", "fingerprint": ":".join(["0F", "3A"] * 16)}
        ]

    def test_keeps_ice_credentials_that_look_numeric_verbatim(self) -> None:
        """sdp_transform coerces ``0012`` to the int ``12``; the credential must reach the gateway as sent."""
        offer = (
            chrome_offer()
            .replace("a=ice-ufrag:nNCO", "a=ice-ufrag:0012")
            .replace("a=ice-pwd:ICEPWD_REDACTED_24chars0", "a=ice-pwd:1e10")
        )

        ortc = offer_to_ortc(offer)

        assert ortc["iceParameters"] == {"iceUfrag": "0012", "icePwd": "1e10"}

    def test_carries_the_cname_an_offered_ssrc_declares(self) -> None:
        offer = with_line_after(chrome_offer(), "a=rtpmap:126 telephone-event/8000", "a=ssrc:1234 cname:browsercname")

        ortc = offer_to_ortc(offer)

        assert ortc["cname"] == "browsercname"

    def test_omits_cname_when_no_ssrc_is_offered(self) -> None:
        ortc = offer_to_ortc(chrome_offer())

        assert "cname" not in ortc


class TestParseOffer:
    def test_keeps_mids_and_bundle_group_as_strings(self) -> None:
        parsed = parse_offer(chrome_offer())

        assert [m["mid"] for m in parsed["media"]] == ["0", "1"]
        assert parsed["groups"] == [{"type": "BUNDLE", "mids": "0 1"}]

    @pytest.mark.parametrize("config", ["0012", "1e10", "nan", "inf"])
    def test_keeps_an_fmtp_config_that_looks_numeric_verbatim(self, config: str) -> None:
        """D24: sdp_transform turns ``a=fmtp:97 0012`` into the int ``12`` (and ``nan``/``inf`` into floats)."""
        offer = chrome_offer().replace("a=fmtp:97 apt=96", f"a=fmtp:97 {config}")

        parsed = parse_offer(offer)

        assert {"payload": 97, "config": config} in parsed["media"][1]["fmtp"]

    @pytest.mark.parametrize("cname", ["0012", "1e10", "nan", "inf"])
    def test_keeps_an_ssrc_value_that_looks_numeric_verbatim(self, cname: str) -> None:
        """D24: sdp_transform turns ``a=ssrc:1 cname:0012`` into the int ``12``."""
        offer = with_line_after(chrome_offer(), "a=rtpmap:96 VP8/90000", f"a=ssrc:1 cname:{cname}")

        ortc = offer_to_ortc(offer)

        assert ortc["cname"] == cname

    def test_leaves_an_ssrc_line_without_a_value_as_parsed(self) -> None:
        offer = with_line_after(chrome_offer(), "a=rtpmap:96 VP8/90000", "a=ssrc:1 cname")

        parsed = parse_offer(offer)

        assert parsed["media"][1]["ssrcs"] == [{"id": 1, "attribute": "cname"}]

    def test_keeps_a_single_payload_list_as_a_string(self) -> None:
        offer = chrome_offer().replace(
            "m=audio 9 UDP/TLS/RTP/SAVPF 111 63 9 0 8 13 110 126", "m=audio 9 UDP/TLS/RTP/SAVPF 111"
        )

        parsed = parse_offer(offer)

        assert parsed["media"][0]["payloads"] == "111"


class TestOfferErrors:
    @pytest.mark.parametrize("offer", ["", "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"])
    def test_rejects_an_offer_without_media_sections(self, offer: str) -> None:
        with pytest.raises(SdpError, match="no media sections"):
            offer_to_ortc(offer)

    def test_rejects_an_offer_without_ice_credentials(self) -> None:
        offer = without_lines(without_lines(chrome_offer(), "a=ice-ufrag:"), "a=ice-pwd:")

        with pytest.raises(SdpError, match="ice-ufrag"):
            offer_to_ortc(offer)

    def test_rejects_an_offer_with_an_ice_ufrag_but_no_password(self) -> None:
        offer = without_lines(chrome_offer(), "a=ice-pwd:")

        with pytest.raises(SdpError, match="ice-pwd"):
            offer_to_ortc(offer)

    def test_rejects_an_offer_without_a_fingerprint(self) -> None:
        offer = without_lines(chrome_offer(), "a=fingerprint:")

        with pytest.raises(SdpError, match="fingerprint"):
            offer_to_ortc(offer)


class TestNegotiatedCaps:
    @pytest.mark.parametrize("bucket", ["sendrecv", "recv", "send"])
    def test_returns_the_first_populated_bucket(self, bucket: str) -> None:
        ortc = gateway_ortc()
        caps = ortc["rtpCapabilities"].pop("sendrecv")
        ortc["rtpCapabilities"] = {"send": {}, "recv": {}, "sendrecv": {}, bucket: caps}

        assert negotiated_caps(ortc) == caps

    def test_prefers_sendrecv_over_recv(self) -> None:
        ortc = gateway_ortc()
        sendrecv = ortc["rtpCapabilities"]["sendrecv"]
        ortc["rtpCapabilities"]["recv"] = gateway_ortc("gateway_join_ortc")["rtpCapabilities"]["sendrecv"]

        assert negotiated_caps(ortc) == sendrecv

    def test_accepts_flat_capabilities(self) -> None:
        ortc = gateway_ortc()
        ortc["rtpCapabilities"] = ortc["rtpCapabilities"]["sendrecv"]

        assert negotiated_caps(ortc) == ortc["rtpCapabilities"]

    @pytest.mark.parametrize("junk", [["videoCodecs"], "sendrecv", 1])
    def test_skips_a_bucket_that_is_not_an_object(self, junk: object) -> None:
        ortc = gateway_ortc()
        caps = ortc["rtpCapabilities"].pop("sendrecv")
        ortc["rtpCapabilities"] = {"sendrecv": junk, "recv": caps}

        assert negotiated_caps(ortc) == caps

    @pytest.mark.parametrize("junk", [["sendrecv"], "caps", None])
    def test_returns_nothing_when_the_capabilities_are_not_an_object(self, junk: object) -> None:
        ortc = gateway_ortc()
        ortc["rtpCapabilities"] = junk

        assert negotiated_caps(ortc) == {}

    def test_returns_nothing_when_the_gateway_sends_no_capabilities(self) -> None:
        ortc = gateway_ortc()
        del ortc["rtpCapabilities"]

        assert negotiated_caps(ortc) == {}
