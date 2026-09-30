"""``RtmRestClient`` against the fake RTM hosts: the real JSON ``_post``, rotation, stickiness and ack codes."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

import pytest

from pyagora.exceptions import RtmError
from pyagora.rtm.client import DEFAULT_ACCEPTED_CODES, RtmRestClient
from tests._helpers import RTM_CREDENTIALS, SECRET_VALUES
from tests.fakegateway._common import LOOPBACK
from tests.integration._helpers import rtm_headers

if TYPE_CHECKING:
    from collections.abc import Mapping

    import aiohttp

    from tests.fakegateway import FakeAgora

TIMEOUT_S = 5.0
PING = {"cmd": "ping"}
WRONG_TOKEN = "rtm-token-wrong"
ROTATED_TOKEN = "rtm-token-rotated"
# Port 1 on loopback has no listener, so the connect is refused: the ClientError branch, not an HTTP status.
UNREACHABLE_HOST = f"http://{LOOPBACK}:1"


async def _send(client: RtmRestClient, payload: Mapping[str, object] = PING, **kwargs: object) -> str:
    return await asyncio.wait_for(client.send_peer_message(payload, **kwargs), timeout=TIMEOUT_S)


class TestSendPeerMessage:
    async def test_returns_message_delivered_when_waiting_for_the_ack(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        code = await _send(client)

        record = fake_agora.state.log.rtm_messages[-1]
        assert code == "message_delivered"
        assert (record.status, record.wait_for_ack) == (200, True)

    async def test_sends_the_three_auth_headers_and_the_compact_payload_string(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        await _send(client)

        record = fake_agora.state.log.rtm_messages[-1]
        assert dict(record.headers) == rtm_headers()
        assert record.body is not None
        assert record.body["payload"] == '{"cmd":"ping"}'
        assert record.body["destination"] == RTM_CREDENTIALS.peer_user_id

    async def test_returns_message_sent_without_waiting_for_the_ack(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        code = await _send(client, wait_for_ack=False)

        assert code == "message_sent"
        assert fake_agora.state.log.rtm_messages[-1].wait_for_ack is False


class TestHostRotation:
    async def test_moves_to_the_second_host_and_sticks_to_it(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(rtm_fail_first=1)
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        await _send(client)
        await _send(client)

        log = fake_agora.state.log.rtm_messages
        assert [(r.host_index, r.status) for r in log] == [(0, 503), (1, 200), (1, 200)]

    async def test_raises_with_the_last_status_when_every_host_fails(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(rtm_fail_first=len(fake_agora.rtm_hosts))
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        with pytest.raises(RtmError) as caught:
            await _send(client)

        assert caught.value.status == 503
        assert [r.host_index for r in fake_agora.state.log.rtm_messages] == [0, 1]

    async def test_a_rejected_token_raises_401_without_trying_the_second_host(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        creds = dataclasses.replace(RTM_CREDENTIALS, token=WRONG_TOKEN)
        client = RtmRestClient(creds, session=raw_http, hosts=fake_agora.rtm_hosts)

        with pytest.raises(RtmError) as caught:
            await _send(client)

        assert caught.value.status == 401
        assert [(r.host_index, r.status) for r in fake_agora.state.log.rtm_messages] == [(0, 401)]
        assert WRONG_TOKEN not in str(caught.value)

    async def test_an_unreachable_host_moves_to_the_next(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=[UNREACHABLE_HOST, *fake_agora.rtm_hosts])

        code = await _send(client)

        assert code == "message_delivered"
        assert [(r.host_index, r.status) for r in fake_agora.state.log.rtm_messages] == [(0, 200)]


class TestAckCodes:
    async def test_an_unaccepted_code_raises_by_default(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(rtm_code="message_offline")
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        with pytest.raises(RtmError) as caught:
            await _send(client)

        assert (caught.value.status, caught.value.code) == (200, "message_offline")

    async def test_a_code_in_accepted_codes_is_returned(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(rtm_code="message_offline")
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)

        code = await _send(client, accepted_codes=DEFAULT_ACCEPTED_CODES | {"message_offline"})

        assert code == "message_offline"


class TestUpdateToken:
    async def test_the_next_request_carries_the_updated_token(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)
        await _send(client)
        fake_agora.state.rtm_token = ROTATED_TOKEN
        with pytest.raises(RtmError) as stale:
            await _send(client)
        assert stale.value.status == 401

        client.update_token(ROTATED_TOKEN)
        code = await _send(client)

        record = fake_agora.state.log.rtm_messages[-1]
        assert code == "message_delivered"
        assert record.headers["x-agora-token"] == ROTATED_TOKEN
        assert record.headers["Authorization"] == f"agora token={ROTATED_TOKEN}"


class TestSessionOwnership:
    async def test_an_owned_session_is_created_and_closed_by_the_context_manager(self, fake_agora: FakeAgora) -> None:
        async with RtmRestClient(RTM_CREDENTIALS, hosts=fake_agora.rtm_hosts) as client:
            code = await _send(client)
            owned = client._http()

        assert code == "message_delivered"
        assert owned.closed

    async def test_a_borrowed_session_stays_open_and_usable(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        async with RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts) as client:
            await _send(client)

        await _send(RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts))

        assert not raw_http.closed
        assert [r.status for r in fake_agora.state.log.rtm_messages] == [200, 200]


class TestSecrets:
    async def test_the_client_repr_carries_no_token_after_a_send_and_a_rotation(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, session=raw_http, hosts=fake_agora.rtm_hosts)
        await _send(client)

        client.update_token(ROTATED_TOKEN)

        text = repr(client)
        assert [s for s in (*SECRET_VALUES, ROTATED_TOKEN) if s in text] == []
