"""The fake's RTM ``peer_messages`` route validates and acks as protocol.md §8 describes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._helpers import RTM_CREDENTIALS
from tests.integration._helpers import rtm_body, rtm_headers, rtm_url

if TYPE_CHECKING:
    import aiohttp

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import Frame


async def _post(
    raw_http: aiohttp.ClientSession, url: str, body: Frame, headers: dict[str, str] | None = None
) -> tuple[int, Frame]:
    async with raw_http.post(url, json=body, headers=rtm_headers() if headers is None else headers) as resp:
        return resp.status, await resp.json(content_type=None) if resp.status != 503 else {}


class TestAcks:
    async def test_acks_message_sent_without_wait_for_ack(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        status, body = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0]), rtm_body())

        assert status == 200
        assert (body["result"], body["code"]) == ("success", "message_sent")

    async def test_acks_message_delivered_with_wait_for_ack(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        _, body = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0], wait_for_ack=True), rtm_body())

        assert body["code"] == "message_delivered"

    async def test_records_the_decoded_payload_and_host(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        await _post(raw_http, rtm_url(fake_agora.rtm_hosts[1], wait_for_ack=True), rtm_body())

        record = fake_agora.state.log.rtm_messages[0]
        assert (record.host_index, record.status, record.wait_for_ack) == (1, 200, True)
        assert record.payload == {"cmd": "start_live", "payload": {"isSD": 0}}
        assert record.body == rtm_body()
        assert record.headers["x-agora-uid"] == RTM_CREDENTIALS.user_id


class TestValidation:
    @pytest.mark.parametrize("header", ["x-agora-token", "x-agora-uid", "Authorization"])
    async def test_rejects_a_missing_auth_header(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, header: str
    ) -> None:
        headers = rtm_headers()
        del headers[header]

        status, body = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0]), rtm_body(), headers)

        assert status == 401
        assert body["result"] == "failed"

    async def test_rejects_an_authorization_header_without_the_agora_prefix(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        headers = rtm_headers() | {"Authorization": f"Bearer {fake_agora.state.rtm_token}"}

        status, _ = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0]), rtm_body(), headers)

        assert status == 401

    async def test_rejects_a_uid_header_that_differs_from_the_path(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        headers = rtm_headers() | {"x-agora-uid": "app_999"}

        status, _ = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0]), rtm_body(), headers)

        assert status == 401

    async def test_answers_404_for_another_project(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        status, _ = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0], app_id="other-app"), rtm_body())

        assert status == 404

    async def test_rejects_a_payload_sent_as_a_nested_object(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        body = rtm_body(payload={"cmd": "start_live"})

        status, reply = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0]), body)

        assert status == 400
        assert "payload" in reply["code"]

    @pytest.mark.parametrize("key", ["destination", "enable_offline_messaging", "enable_historical_messaging"])
    async def test_rejects_a_body_missing_a_required_key(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, key: str
    ) -> None:
        body = rtm_body()
        del body[key]

        status, reply = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0]), body)

        assert status == 400
        assert key in reply["code"]

    async def test_rejects_a_user_the_project_does_not_know(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        headers = rtm_headers() | {"x-agora-uid": "app_999"}

        status, reply = await _post(raw_http, rtm_url(fake_agora.rtm_hosts[0], user_id="app_999"), rtm_body(), headers)

        assert status == 401
        assert reply["code"] == "unknown user"
