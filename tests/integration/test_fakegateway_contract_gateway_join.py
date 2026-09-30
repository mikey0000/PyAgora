"""The fake gateway answers ``join_v3`` as protocol.md §2 reconstructs it."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tests._helpers import CREDENTIALS
from tests.integration._helpers import (
    AP_UID,
    RECV_TIMEOUT_S,
    gateway_client,
    join_v3,
    recv_frame,
    request,
    send,
    subscribe,
)

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from tests.fakegateway import FakeAgora


class TestJoinSuccess:
    async def test_echoes_the_request_id_with_a_success_result(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3("a1b2c3"))

        reply = await recv_frame(raw_ws)

        assert (reply["_id"], reply["_result"]) == ("a1b2c3", "success")
        assert "_type" not in reply

    async def test_carries_session_identity_and_rejoin_token(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        await send(raw_ws, join_v3())

        message = (await recv_frame(raw_ws))["_message"]

        assert message["uid"] == AP_UID
        assert (message["cid"], message["vid"]) == (fake_agora.state.cid, fake_agora.state.vid)
        assert message["cname"] == CREDENTIALS.channel_name
        assert message["rejoin_token"] == "rejoin-token-not-real"

    async def test_falls_back_to_the_viewer_uid_without_an_ap_response(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        frame = join_v3()
        del frame["_message"]["ap_response"]
        await send(raw_ws, frame)

        message = (await recv_frame(raw_ws))["_message"]

        assert message["uid"] == fake_agora.state.viewer_uid

    async def test_ortc_is_ice_lite_with_one_host_candidate(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        ice = (await recv_frame(raw_ws))["_message"]["ortc"]["iceParameters"]

        assert (ice["iceUfrag"], len(ice["icePwd"])) == ("FkUf", 24)
        assert ice["candidates"] == [
            {
                "foundation": "udpcandidate",
                "ip": "127.0.0.1",
                "port": 4707,
                "priority": 2103266323,
                "protocol": "udp",
                "type": "host",
            }
        ]

    async def test_ortc_declares_the_dtls_role_server_with_a_full_fingerprint(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        dtls = (await recv_frame(raw_ws))["_message"]["ortc"]["dtlsParameters"]

        assert dtls["role"] == "server"
        assert dtls["fingerprints"][0]["algorithm"] == "sha-256"
        assert len(dtls["fingerprints"][0]["fingerprint"].split(":")) == 32

    async def test_ortc_lists_vp8_h264_h265_and_opus_without_rtx(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        caps = (await recv_frame(raw_ws))["_message"]["ortc"]["rtpCapabilities"]["sendrecv"]

        assert [c["rtpMap"]["encodingName"] for c in caps["videoCodecs"]] == ["VP8", "H264", "H265"]
        assert [c["rtpMap"]["encodingName"] for c in caps["audioCodecs"]] == ["opus"]
        assert all({"type": "nack", "parameter": "pli"} in c["rtcpFeedbacks"] for c in caps["videoCodecs"])

    async def test_ortc_lists_the_mid_extension(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        caps = (await recv_frame(raw_ws))["_message"]["ortc"]["rtpCapabilities"]["sendrecv"]

        mid = "urn:ietf:params:rtp-hdrext:sdes:mid"
        assert mid in [e["extensionName"] for e in caps["videoExtensions"]]
        assert mid in [e["extensionName"] for e in caps["audioExtensions"]]

    async def test_lists_the_online_device_stream(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        streams = (await recv_frame(raw_ws))["_message"]["streams"]

        assert streams == [
            {
                "uid": 1,
                "uint_id": 1,
                "video": True,
                "ssrcId": 44444444,
                "rtxSsrcId": 44444445,
                "cname": "fake-device-cname",
                "codec": "h264",
                "pt": 102,
            }
        ]

    async def test_lists_no_streams_while_the_device_is_offline(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(device_online=False)
        await send(raw_ws, join_v3())

        message = (await recv_frame(raw_ws))["_message"]

        assert "streams" not in message

    async def test_records_the_join_as_received(self, fake_agora: FakeAgora, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)

        assert fake_agora.state.log.received_of_type("join_v3") == [join_v3()]


class TestJoinFailure:
    @pytest.mark.parametrize(
        ("key", "value", "code"),
        [("app_id", "other-app", 2013), ("channel_name", "other-channel", 2014), ("channel_key", "wrong-token", 110)],
    )
    async def test_rejects_credentials_it_does_not_accept(
        self, raw_ws: ClientConnection, key: str, value: str, code: int
    ) -> None:
        await send(raw_ws, join_v3("a1b2c3", **{key: value}))

        reply = await recv_frame(raw_ws)

        assert (reply["_id"], reply["_result"]) == ("a1b2c3", "failed")
        assert reply["_message"]["error_code"] == code
        assert isinstance(reply["_message"]["error_str"], str)

    async def test_rejects_a_join_without_ortc_naming_the_key(self, raw_ws: ClientConnection) -> None:
        frame = join_v3()
        del frame["_message"]["ortc"]
        await send(raw_ws, frame)

        reply = await recv_frame(raw_ws)

        assert reply["_message"]["error_code"] == 2022
        assert "ortc" in reply["_message"]["error_str"]

    async def test_rejects_a_join_without_a_message(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, request("join_v3"))

        reply = await recv_frame(raw_ws)

        assert (reply["_result"], reply["_message"]["error_code"]) == ("failed", 2022)

    async def test_a_rejected_join_leaves_the_socket_unjoined(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3(channel_key="wrong-token"))
        await recv_frame(raw_ws)
        await send(raw_ws, subscribe())

        reply = await recv_frame(raw_ws)

        assert reply["_message"]["error_code"] == 2011


class TestRepeatJoin:
    async def test_a_second_join_on_the_same_uid_quits_the_first_with_2003(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        async with gateway_client(fake_agora.gateway_url) as second:
            await send(second, join_v3("c3d4e5"))

            quit_frame = await recv_frame(joined_ws)
            second_reply = await recv_frame(second)

        assert quit_frame["_type"] == "on_notification"
        assert (quit_frame["_message"]["action"], quit_frame["_message"]["code"]) == ("quit", 2003)
        assert second_reply["_result"] == "success"

    async def test_a_join_on_another_uid_leaves_the_first_alone(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        async with gateway_client(fake_agora.gateway_url) as second:
            await send(second, join_v3(ap_uid=AP_UID + 1))
            await recv_frame(second)

        assert [r.connection for r in fake_agora.state.log.gateway_sent] == [0, 1]


class TestJoinDelay:
    async def test_holds_the_reply_until_the_fake_clock_passes_the_delay(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(join_delay_s=5.0)
        await send(raw_ws, join_v3())
        await asyncio.wait_for(fake_agora.scheduler.wait_for_sleepers(1), timeout=RECV_TIMEOUT_S)

        await fake_agora.advance(4.9)
        sent_before = list(fake_agora.state.log.gateway_sent)
        await fake_agora.advance(0.1)
        reply = await recv_frame(raw_ws)

        assert sent_before == []
        assert reply["_result"] == "success"
