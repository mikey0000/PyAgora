from __future__ import annotations

import asyncio
from collections import deque
import dataclasses
import logging
from typing import Any

import aiohttp
import pytest

from pyagora.const import RTM_HOSTS
from pyagora.exceptions import RtmError
from pyagora.rtm import RtmRestClient
from tests._helpers import RTM_CREDENTIALS, RTM_TOKEN, leaked_secrets, load_fixture, load_json_fixture
from tests.unit._fakes import FakeMisuseError, RecordingHttpSession

HOSTS = ("https://rtm-a.test", "https://rtm-b.test", "https://rtm-c.test")
PAYLOAD = {"cmd": "ping"}
NEW_TOKEN = "rtm-token-rotated-not-real"

type Outcome = tuple[int, object] | BaseException


class RecordedPoster:
    """Stands in for ``RtmRestClient._post``: records each request, answers from a script."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.outcomes: deque[Outcome] = deque(outcomes)
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    @property
    def urls(self) -> list[str]:
        return [url for url, _, _ in self.calls]

    async def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, object]:
        self.calls.append((url, headers, body))
        if not self.outcomes:
            raise FakeMisuseError(f"no response scripted for {url}")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class GatedPoster(RecordedPoster):
    """A ``RecordedPoster`` whose first request is held until ``release`` is set."""

    def __init__(self, *outcomes: Outcome) -> None:
        super().__init__(*outcomes)
        self.first_started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, object]:
        if not self.first_started.is_set():
            self.first_started.set()
            await self.release.wait()
        return await super().__call__(url, headers, body)


def ack(name: str) -> tuple[int, object]:
    return 200, load_json_fixture(f"rtm/{name}.json")


def make_client(*outcomes: Outcome, hosts: tuple[str, ...] | None = HOSTS) -> tuple[RtmRestClient, RecordedPoster]:
    client = RtmRestClient(RTM_CREDENTIALS) if hosts is None else RtmRestClient(RTM_CREDENTIALS, hosts=hosts)
    poster = RecordedPoster(*outcomes)
    client._post = poster
    return client, poster


class TestRequestShape:
    async def test_posts_the_recorded_url_headers_and_body(self) -> None:
        expected = load_json_fixture("rtm/peer_message_request_expected.json")
        client, poster = make_client(ack("ack_delivered"))

        await client.send_peer_message(expected["payload"])

        url, headers, body = poster.calls[0]
        assert url == f"{HOSTS[0]}{expected['path']}{expected['query']}"
        assert headers == {k: v.replace("TOKEN_REDACTED", RTM_TOKEN) for k, v in expected["headers"].items()}
        assert body == expected["body"]

    async def test_omits_the_ack_query_when_not_waiting(self) -> None:
        expected = load_json_fixture("rtm/peer_message_request_expected.json")
        client, poster = make_client(ack("ack_sent"))

        await client.send_peer_message(PAYLOAD, wait_for_ack=False)

        assert poster.urls == [f"{HOSTS[0]}{expected['path']}"]

    async def test_url_quotes_the_user_id(self) -> None:
        creds = dataclasses.replace(RTM_CREDENTIALS, user_id="app/1@x y")
        client = RtmRestClient(creds, hosts=HOSTS)
        poster = RecordedPoster(ack("ack_sent"))
        client._post = poster

        await client.send_peer_message(PAYLOAD, wait_for_ack=False)

        assert poster.urls == [f"{HOSTS[0]}/dev/v2/project/app-id-test/rtm/users/app%2F1%40x%20y/peer_messages"]

    async def test_uses_the_shipped_hosts_by_default(self) -> None:
        client, poster = make_client((503, {}), ack("ack_sent"), hosts=None)

        await client.send_peer_message(PAYLOAD)

        assert [url.split("/dev/")[0] for url in poster.urls] == list(RTM_HOSTS)


class TestAckCodes:
    @pytest.mark.parametrize(
        ("fixture", "code"), [("ack_delivered", "message_delivered"), ("ack_sent", "message_sent")]
    )
    async def test_returns_the_ack_code_for_a_default_accepted_code(self, fixture: str, code: str) -> None:
        client, _ = make_client(ack(fixture))

        assert await client.send_peer_message(PAYLOAD) == code

    async def test_raises_on_an_offline_peer_under_the_default_codes_without_rotating(self) -> None:
        client, poster = make_client(ack("ack_offline"))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert (info.value.status, info.value.code) == (200, "message_offline")
        assert len(poster.calls) == 1

    async def test_accepts_an_offline_peer_when_the_caller_allows_it(self) -> None:
        client, _ = make_client(ack("ack_offline"))

        code = await client.send_peer_message(
            PAYLOAD,
            wait_for_ack=False,
            accepted_codes=frozenset({"message_sent", "message_delivered", "message_offline"}),
        )

        assert code == "message_offline"

    async def test_raises_on_a_failed_result_without_rotating(self) -> None:
        client, poster = make_client(ack("ack_failed"))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert info.value.code == "message_failure"
        assert len(poster.calls) == 1

    async def test_raises_on_a_failed_result_even_with_an_accepted_code(self) -> None:
        client, poster = make_client(ack("ack_failed_sent"))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert info.value.code == "message_sent"
        assert len(poster.calls) == 1

    async def test_matches_caller_accepted_codes_case_insensitively(self) -> None:
        client, _ = make_client(ack("ack_offline"))

        code = await client.send_peer_message(PAYLOAD, accepted_codes=frozenset({"Message_Offline"}))

        assert code == "message_offline"

    async def test_matches_result_and_code_case_insensitively(self) -> None:
        client, _ = make_client(ack("ack_mixed_case"))

        assert await client.send_peer_message(PAYLOAD) == "message_delivered"

    async def test_raises_when_a_success_status_carries_no_json_body(self) -> None:
        client, _ = make_client((200, None))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert (info.value.status, info.value.code) == (200, None)

    async def test_raises_on_a_client_error_status_without_trying_the_next_host(self) -> None:
        client, poster = make_client((401, {"result": "failed"}))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert info.value.status == 401
        assert len(poster.calls) == 1


class TestEndpointRotation:
    @pytest.mark.parametrize(
        "failure",
        [(404, {}), (429, {}), (500, None), (503, {}), aiohttp.ClientConnectionError("refused"), TimeoutError()],
        ids=["404", "429", "500", "503", "client-error", "timeout"],
    )
    async def test_moves_to_the_next_host_on_a_retryable_failure(self, failure: Outcome) -> None:
        client, poster = make_client(failure, ack("ack_delivered"))

        code = await client.send_peer_message(PAYLOAD)

        assert code == "message_delivered"
        assert [url.split("/dev/")[0] for url in poster.urls] == [HOSTS[0], HOSTS[1]]

    async def test_raises_with_the_last_status_after_every_host_fails(self) -> None:
        client, poster = make_client((404, {}), TimeoutError(), (503, {}))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert info.value.status == 503
        assert [url.split("/dev/")[0] for url in poster.urls] == list(HOSTS)

    async def test_raises_without_a_status_when_the_last_host_was_unreachable(self) -> None:
        client, _ = make_client((503, {}), (429, {}), aiohttp.ClientConnectionError("refused"))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert info.value.status is None

    async def test_tries_the_last_good_host_first_on_the_next_call(self) -> None:
        client, poster = make_client((503, {}), (404, {}), ack("ack_sent"), ack("ack_sent"))
        await client.send_peer_message(PAYLOAD)

        await client.send_peer_message(PAYLOAD)

        assert poster.urls[3].startswith(HOSTS[2])

    async def test_falls_back_to_the_remaining_hosts_in_order_after_the_last_good_one(self) -> None:
        client, poster = make_client((503, {}), ack("ack_sent"), (503, {}), (503, {}), ack("ack_sent"))
        await client.send_peer_message(PAYLOAD)

        await client.send_peer_message(PAYLOAD)

        assert [url.split("/dev/")[0] for url in poster.urls[2:]] == [HOSTS[1], HOSTS[0], HOSTS[2]]


class TestSerialisation:
    async def test_a_second_message_waits_for_the_first_to_finish(self) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, hosts=HOSTS)
        poster = GatedPoster(ack("ack_sent"), ack("ack_sent"))
        client._post = poster
        first = asyncio.create_task(client.send_peer_message({"n": 1}))
        await asyncio.wait_for(poster.first_started.wait(), timeout=1)
        second = asyncio.create_task(client.send_peer_message({"n": 2}))
        await asyncio.sleep(0)

        recorded_while_first_in_flight = len(poster.calls)
        poster.release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

        assert recorded_while_first_in_flight == 0
        assert [body["payload"] for _, _, body in poster.calls] == ['{"n":1}', '{"n":2}']


class TestUpdateToken:
    async def test_the_next_request_carries_the_new_token(self) -> None:
        client, poster = make_client(ack("ack_sent"))
        client.update_token(NEW_TOKEN)

        await client.send_peer_message(PAYLOAD)

        headers = poster.calls[0][1]
        assert headers["x-agora-token"] == NEW_TOKEN
        assert headers["Authorization"] == f"agora token={NEW_TOKEN}"


class TestHttpSeam:
    """D10 and the response decoding, observed at the aiohttp session the client borrows."""

    @pytest.mark.parametrize(("kwargs", "ssl"), [({}, True), ({"verify_ssl": False}, False)], ids=["default", "off"])
    async def test_passes_the_verify_flag_to_aiohttp(self, kwargs: dict[str, bool], ssl: bool) -> None:
        http = RecordingHttpSession((200, load_fixture("rtm/ack_sent.json")))
        client = RtmRestClient(RTM_CREDENTIALS, http, hosts=HOSTS, **kwargs)

        await client.send_peer_message(PAYLOAD)

        assert [call_kwargs["ssl"] for _, call_kwargs in http.calls] == [ssl]

    async def test_a_body_that_is_not_utf8_is_refused_as_not_json(self) -> None:
        http = RecordingHttpSession((200, b"\xff not json"))
        client = RtmRestClient(RTM_CREDENTIALS, http, hosts=HOSTS)

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert (info.value.status, info.value.code) == (200, None)


class TestSessionLifetime:
    async def test_does_not_close_a_borrowed_session(self) -> None:
        async with aiohttp.ClientSession() as session:
            client = RtmRestClient(RTM_CREDENTIALS, session, hosts=HOSTS)

            await client.close()

            assert client._http() is session
            assert not session.closed

    async def test_closes_its_own_session_on_close(self) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, hosts=HOSTS)
        owned = client._http()

        await client.close()

        assert owned.closed

    async def test_closes_its_own_session_on_context_exit(self) -> None:
        async with RtmRestClient(RTM_CREDENTIALS, hosts=HOSTS) as client:
            owned = client._http()

        assert owned.closed

    async def test_close_is_idempotent(self) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, hosts=HOSTS)
        owned = client._http()

        await client.close()
        await client.close()

        assert owned.closed
        assert client._session is None

    async def test_memoises_its_own_session(self) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, hosts=HOSTS)

        first, second = client._http(), client._http()
        await client.close()

        assert first is second


class TestSecrets:
    def test_repr_carries_no_token_before_or_after_rotation(self) -> None:
        client = RtmRestClient(RTM_CREDENTIALS, hosts=HOSTS)
        before = repr(client)
        client.update_token(NEW_TOKEN)

        after = repr(client)

        assert RTM_TOKEN not in before
        assert NEW_TOKEN not in after
        assert RTM_TOKEN not in after

    async def test_errors_carry_no_token(self) -> None:
        client, _ = make_client((503, {}), (404, {}), aiohttp.ClientConnectionError("refused"))

        with pytest.raises(RtmError) as info:
            await client.send_peer_message(PAYLOAD)

        assert RTM_TOKEN not in str(info.value)
        assert RTM_TOKEN not in repr(info.value)

    async def test_logs_carry_no_token_across_rotation_and_rejection(self, caplog: pytest.LogCaptureFixture) -> None:
        client, _ = make_client(TimeoutError(), (503, {}), ack("ack_failed"))
        client.update_token(NEW_TOKEN)

        with caplog.at_level(logging.DEBUG, logger="pyagora.rtm"), pytest.raises(RtmError):
            await client.send_peer_message(PAYLOAD)

        messages = [r.getMessage() for r in caplog.records]
        assert messages
        assert leaked_secrets(caplog.records) == []
        assert not any(NEW_TOKEN in m for m in messages)
