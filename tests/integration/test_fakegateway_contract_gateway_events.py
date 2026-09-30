"""After join, the fake gateway acks requests and pushes the events protocol.md §3-§5 list."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from tests.integration._helpers import recv_frame, request, send, subscribe, upload

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from tests.fakegateway import FakeAgora


class TestRequestAcks:
    async def test_acks_a_subscribe_to_the_device_stream(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, subscribe(1, "b2c3d4"))

        reply = await recv_frame(joined_ws)

        assert reply == {"_id": "b2c3d4", "_result": "success", "_message": {"stream_id": 1}}

    async def test_fails_a_subscribe_to_an_unknown_uid(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, subscribe(2))

        reply = await recv_frame(joined_ws)

        assert (reply["_result"], reply["_message"]["error_code"]) == ("failed", 2021)

    async def test_fails_a_subscribe_while_the_device_is_offline(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        fake_agora.control(device_online=False)
        await send(joined_ws, subscribe(1))

        reply = await recv_frame(joined_ws)

        assert reply["_message"]["error_code"] == 2021

    async def test_fails_a_subscribe_before_join_with_not_joined(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, subscribe(1))

        reply = await recv_frame(raw_ws)

        assert reply["_message"]["error_code"] == 2011

    async def test_fails_a_subscribe_without_a_stream_id(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("subscribe", {"stream_type": "video"}))

        reply = await recv_frame(joined_ws)

        assert "stream_id" in reply["_message"]["error_str"]

    async def test_fails_a_renew_token_before_join_with_not_joined(self, raw_ws: ClientConnection) -> None:
        await send(raw_ws, request("renew_token", {"token": "renewed-token"}))

        reply = await recv_frame(raw_ws)

        assert reply["_message"]["error_code"] == 2011

    async def test_acks_unsubscribe(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("unsubscribe", {"p2p_id": 1, "ortc": [], "stream_id": 1}, "c3d4e5"))

        reply = await recv_frame(joined_ws)

        assert (reply["_id"], reply["_result"]) == ("c3d4e5", "success")

    async def test_answers_ping_with_a_correlated_empty_success(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("ping", request_id="f6a7b8"))

        reply = await recv_frame(joined_ws)

        assert reply == {"_id": "f6a7b8", "_result": "success", "_message": {}}

    async def test_does_not_answer_a_ping_back_upload(self, fake_agora: FakeAgora, joined_ws: ClientConnection) -> None:
        await send(joined_ws, upload("ping_back", {"pingpongElapse": 42}))
        await send(joined_ws, request("ping", request_id="f6a7b8"))

        reply = await recv_frame(joined_ws)

        assert reply["_id"] == "f6a7b8"
        assert fake_agora.state.log.received_of_type("ping_back") == [upload("ping_back", {"pingpongElapse": 42})]

    async def test_acks_renew_token(self, fake_agora: FakeAgora, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("renew_token", {"token": "renewed-token"}, "a7b8c9"))

        reply = await recv_frame(joined_ws)

        assert (reply["_id"], reply["_result"]) == ("a7b8c9", "success")
        assert fake_agora.state.log.received_of_type("renew_token")[0]["_message"] == {"token": "renewed-token"}

    async def test_fails_a_renew_token_without_a_token(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("renew_token", {}))

        reply = await recv_frame(joined_ws)

        assert reply["_result"] == "failed"

    async def test_acks_and_records_set_client_role(self, fake_agora: FakeAgora, joined_ws: ClientConnection) -> None:
        role = {"role": "host", "level": 0, "client_ts": 1790716796000}
        await send(joined_ws, request("set_client_role", role, "e5f6a7"))

        reply = await recv_frame(joined_ws)

        assert reply["_result"] == "success"
        assert fake_agora.state.log.received_of_type("set_client_role") == [request("set_client_role", role, "e5f6a7")]

    async def test_leave_closes_the_socket_cleanly(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("leave", request_id="b8c9d0"))

        with pytest.raises(ConnectionClosedOK):
            await recv_frame(joined_ws)

    async def test_answers_an_unknown_request_with_an_error_event(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, request("warp_drive", {}))

        reply = await recv_frame(joined_ws)

        assert reply["_type"] == "error"
        assert "warp_drive" in reply["_message"]["error"]

    async def test_answers_a_non_json_frame_with_an_error_event(self, joined_ws: ClientConnection) -> None:
        await send(joined_ws, "not json")

        reply = await recv_frame(joined_ws)

        assert reply["_type"] == "error"


class TestServerEvents:
    async def test_announce_peer_sends_user_online_then_video_stream(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        await fake_agora.announce_peer()

        first, second = await recv_frame(joined_ws), await recv_frame(joined_ws)

        assert first == {"_type": "on_user_online", "_message": {"uid": 1}}
        assert second["_type"] == "on_add_video_stream"
        assert second["_message"] == fake_agora.state.device.video_stream()

    async def test_announce_peer_can_send_the_stream_before_the_user(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        await fake_agora.announce_peer(stream_first=True)

        types = [(await recv_frame(joined_ws))["_type"], (await recv_frame(joined_ws))["_type"]]

        assert types == ["on_add_video_stream", "on_user_online"]

    async def test_announce_peer_brings_the_device_online(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        fake_agora.control(device_online=False)

        await fake_agora.announce_peer()

        assert fake_agora.state.device_online

    async def test_peer_leaves_sends_user_offline_and_takes_the_device_offline(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        await fake_agora.peer_leaves()

        frame = await recv_frame(joined_ws)

        assert frame == {"_type": "on_user_offline", "_message": {"uid": 1, "reason": "quit"}}
        assert not fake_agora.state.device_online

    async def test_send_quit_sends_the_repeat_join_notification(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        await fake_agora.send_quit()

        frame = await recv_frame(joined_ws)

        assert frame == {
            "_type": "on_notification",
            "_message": {"action": "quit", "code": 2003, "detail": "ERR_REPEAT_JOIN"},
        }

    async def test_send_p2p_lost_puts_the_error_inside_the_message(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        await fake_agora.send_p2p_lost()

        frame = await recv_frame(joined_ws)

        assert frame == {"_type": "on_p2p_lost", "_message": {"error_code": 1, "error_str": "stun timeout"}}

    async def test_sends_token_will_expire_and_did_expire(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        await fake_agora.send_token_will_expire()
        await fake_agora.send_token_did_expire()

        types = [(await recv_frame(joined_ws))["_type"], (await recv_frame(joined_ws))["_type"]]

        assert types == ["on_token_privilege_will_expire", "on_token_privilege_did_expire"]

    async def test_drop_socket_aborts_without_a_close_frame(
        self, fake_agora: FakeAgora, joined_ws: ClientConnection
    ) -> None:
        fake_agora.drop_socket()

        with pytest.raises(ConnectionClosedError):
            await recv_frame(joined_ws)

    async def test_events_reach_only_joined_sockets(self, fake_agora: FakeAgora, raw_ws: ClientConnection) -> None:
        await fake_agora.announce_peer()
        await send(raw_ws, request("ping", request_id="f6a7b8"))

        reply = await recv_frame(raw_ws)

        assert reply["_id"] == "f6a7b8"
