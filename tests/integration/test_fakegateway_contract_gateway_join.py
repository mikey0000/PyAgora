"""The fake gateway answers ``join_v3`` in the shape captured on 2026-10-01 (``fixtures/gateway/real/``)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tests._helpers import load_json_fixture
from tests.fakegateway._common import CID, LOOPBACK, edge_fingerprint
from tests.integration._helpers import (
    AP_UID,
    RECV_TIMEOUT_S,
    gateway_client,
    join_v3,
    recv_frame,
    recv_join_followups,
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

    async def test_carries_the_captured_keys_with_uid_vid_and_rejoin_token(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        await send(raw_ws, join_v3())

        message = (await recv_frame(raw_ws))["_message"]

        assert sorted(message) == sorted(load_json_fixture("gateway/real/join_ok_luba2.json")["_message"])
        assert (message["uid"], message["vid"]) == (AP_UID, fake_agora.state.vid)
        assert message["rejoin_token"] == "rejoin-token-not-real"

    async def test_follows_the_result_with_capabilities_then_the_publishing_device(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)

        capabilities, online, stream = await recv_join_followups(raw_ws)

        assert capabilities == load_json_fixture("gateway/real/on_rtp_capability_change.json")
        assert online["_message"] == {"uid": fake_agora.state.device.uid}
        assert sorted(stream["_message"]) == sorted(
            load_json_fixture("gateway/real/on_add_video_stream.json")["_message"]
        )

    async def test_falls_back_to_the_viewer_uid_without_an_ap_response(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        frame = join_v3()
        del frame["_message"]["ap_response"]
        await send(raw_ws, frame)

        message = (await recv_frame(raw_ws))["_message"]

        assert message["uid"] == fake_agora.state.viewer_uid

    async def test_ortc_is_ice_lite_with_a_v4_and_a_v6_host_candidate(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        ice = (await recv_frame(raw_ws))["_message"]["ortc"]["iceParameters"]

        assert (ice["iceUfrag"].split("_")[0], len(ice["icePwd"])) == (str(CID), 24)
        assert [(c["ip"], c["port"], c["type"]) for c in ice["candidates"]] == [
            (LOOPBACK, 4707, "host"),
            ("::1", 4707, "host"),
        ]

    async def test_ortc_takes_the_client_role_with_the_connected_edges_fingerprint(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        await send(raw_ws, join_v3())

        dtls = (await recv_frame(raw_ws))["_message"]["ortc"]["dtlsParameters"]

        edge = fake_agora.state.gateway_edges[0]
        assert dtls == {
            "fingerprints": [{"algorithm": "sha-256", "fingerprint": edge_fingerprint(edge.ip, edge.port)}],
            "role": "client",
        }

    async def test_ortc_has_one_sendrecv_bucket_with_rtx_for_every_video_codec(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        caps = (await recv_frame(raw_ws))["_message"]["ortc"]["rtpCapabilities"]

        video = caps["sendrecv"]["videoCodecs"]
        media = {c["payloadType"] for c in video if c["rtpMap"]["encodingName"] != "rtx"}
        assert list(caps) == ["sendrecv"]
        assert {int(c["fmtp"]["parameters"]["apt"]) for c in video if c["rtpMap"]["encodingName"] == "rtx"} == media
        assert [c["rtpMap"]["encodingName"] for c in caps["sendrecv"]["audioCodecs"]] == ["opus"]

    async def test_ortc_lists_the_mid_extension_for_video_only(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        caps = (await recv_frame(raw_ws))["_message"]["ortc"]["rtpCapabilities"]["sendrecv"]

        mid = "urn:ietf:params:rtp-hdrext:sdes:mid"
        assert mid in [e["extensionName"] for e in caps["videoExtensions"]]
        assert mid not in [e["extensionName"] for e in caps["audioExtensions"]]

    async def test_lists_no_streams_in_the_result(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, join_v3())

        message = (await recv_frame(raw_ws))["_message"]

        assert "streams" not in message

    async def test_announces_nothing_after_the_result_while_the_device_is_offline(
        self, fake_agora: FakeAgora, raw_ws: ClientConnection
    ) -> None:
        fake_agora.control(device_online=False)
        await send(raw_ws, join_v3())
        await recv_frame(raw_ws)

        await recv_join_followups(raw_ws, announced=False)
        await send(raw_ws, request("ping", request_id="f6a7b8"))

        assert (await recv_frame(raw_ws))["_id"] == "f6a7b8"

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

        assert quit_frame == load_json_fixture("gateway/real/on_notification_quit_repeat_join.json")
        assert second_reply["_result"] == "success"

    async def test_a_join_on_another_uid_leaves_the_first_alone(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        async with gateway_client(fake_agora.gateway_url) as second:
            await send(second, join_v3(ap_uid=AP_UID + 1))
            await recv_frame(second)
            await recv_join_followups(second)

        assert fake_agora.state.log.sent_of_type("on_notification") == []


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
