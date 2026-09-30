from __future__ import annotations

from collections import deque
import dataclasses
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from pyagora.ap.client import AgoraAPClient, build_request_payload, encode_request
from pyagora.const import AP_HOSTS, AP_PATH, AP_URI_CHOOSE_SERVER, AP_URI_UPDATE_TICKET
from pyagora.exceptions import APError, APRejectedError
from pyagora.models import EdgeAddress
from tests._helpers import CREDENTIALS, RTC_TOKEN, load_fixture, load_json_fixture
from tests.unit._fakes import FakeMisuseError, RecordingHttpSession

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyagora.models import ChannelCredentials

HOSTS = ("https://ap-1.test", "https://ap-2.test", "https://ap-3.test")
FIXED_NOW = 1790716794.25
SID = 152075528
OPID = 999972753885
EDGES = (EdgeAddress(ip="203.0.113.10", port=4713), EdgeAddress(ip="203.0.113.11", port=4713))


type Outcome = tuple[int, object] | BaseException


class RecordedPoster:
    """Stands in for ``AgoraAPClient._post``: records each request, answers from a script."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.outcomes: deque[Outcome] = deque(outcomes)
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    @property
    def urls(self) -> list[str]:
        return [url for url, _, _ in self.calls]

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [body for _, _, body in self.calls]

    async def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, object]:
        self.calls.append((url, headers, body))
        if not self.outcomes:
            raise FakeMisuseError(f"no response scripted for {url}")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def ok(name: str = "choose_server_response") -> tuple[int, object]:
    return 200, load_json_fixture(f"ap/{name}.json")


def _ids(*values: int) -> Callable[[], int]:
    return iter(values).__next__


def make_ap_client(
    *outcomes: Outcome, ids: Callable[[], int] | None = None, **kwargs: Any
) -> tuple[AgoraAPClient, RecordedPoster]:
    kwargs.setdefault("hosts", HOSTS)
    client = AgoraAPClient(clock=lambda: FIXED_NOW, id_factory=ids or _ids(SID, OPID), **kwargs)
    poster = RecordedPoster(*outcomes)
    client._post = poster
    return client, poster


def _url(host: str) -> str:
    return f"{host}{AP_PATH}?v=2"


class TestBuildRequestPayload:
    def test_matches_the_reference_request_byte_for_byte(self, credentials: ChannelCredentials) -> None:
        payload = build_request_payload(
            credentials,
            uri=AP_URI_CHOOSE_SERVER,
            service_ids=(11, 26),
            sid=str(SID),
            opid=OPID,
            client_ts=int(FIXED_NOW * 1000),
        )

        assert encode_request(payload) == load_fixture("ap/choose_server_request_expected.json").rstrip("\n")

    def test_sends_detail_6_when_the_credentials_carry_a_string_uid(self) -> None:
        creds = dataclasses.replace(CREDENTIALS, string_uid="viewer-1")

        payload = build_request_payload(creds, uri=22, service_ids=(11,), sid="1", opid=2, client_ts=3)

        assert payload["request_bodies"][0]["buffer"]["detail"] == {
            "11": "CN,GLOBAL",
            "17": "1",
            "22": "CN,GLOBAL",
            "6": "viewer-1",
        }

    def test_takes_the_area_code_from_the_credentials(self) -> None:
        creds = dataclasses.replace(CREDENTIALS, area_code="EU")

        payload = build_request_payload(creds, uri=22, service_ids=(11,), sid="1", opid=2, client_ts=3)

        detail = payload["request_bodies"][0]["buffer"]["detail"]
        assert (detail["11"], detail["22"]) == ("EU", "EU")

    def test_sends_the_role_as_detail_17(self, credentials: ChannelCredentials) -> None:
        payload = build_request_payload(credentials, uri=22, service_ids=(11,), sid="1", opid=2, client_ts=3, role=2)

        assert payload["request_bodies"][0]["buffer"]["detail"]["17"] == "2"

    def test_omits_detail_17_when_the_role_is_zero(self, credentials: ChannelCredentials) -> None:
        payload = build_request_payload(credentials, uri=22, service_ids=(11,), sid="1", opid=2, client_ts=3, role=0)

        assert "17" not in payload["request_bodies"][0]["buffer"]["detail"]

    def test_lists_edges_as_ip_port_pairs(self, credentials: ChannelCredentials) -> None:
        payload = build_request_payload(
            credentials, uri=28, service_ids=(11,), sid="1", opid=2, client_ts=3, edges=EDGES
        )

        assert payload["request_bodies"][0]["buffer"]["edges_services"] == [
            {"ip": "203.0.113.10", "port": 4713},
            {"ip": "203.0.113.11", "port": 4713},
        ]

    def test_omits_edges_services_when_there_are_no_edges(self, credentials: ChannelCredentials) -> None:
        payload = build_request_payload(credentials, uri=22, service_ids=(11,), sid="1", opid=2, client_ts=3)

        assert "edges_services" not in payload["request_bodies"][0]["buffer"]


class TestChooseServer:
    async def test_posts_the_reference_request_to_the_first_host(self, credentials: ChannelCredentials) -> None:
        client, poster = make_ap_client(ok())

        async with client:
            await client.choose_server(credentials)

        assert poster.urls == [_url(HOSTS[0])]
        assert encode_request(poster.bodies[0]) == load_fixture("ap/choose_server_request_expected.json").rstrip("\n")

    async def test_returns_the_parsed_response(self, credentials: ChannelCredentials) -> None:
        client, _ = make_ap_client(ok())

        async with client:
            response = await client.choose_server(credentials)

        assert response.primary_flag == 4096
        assert len(response.get_turn_addresses()) == 3

    async def test_a_given_sid_is_sent_and_ids_are_only_drawn_for_the_opid(
        self, credentials: ChannelCredentials
    ) -> None:
        client, poster = make_ap_client(ok(), ids=_ids(77))

        async with client:
            await client.choose_server(credentials, sid="s-1")

        assert (poster.bodies[0]["sid"], poster.bodies[0]["opid"]) == ("s-1", 77)

    async def test_a_proxy_wraps_the_host_in_the_proxy_url(self, credentials: ChannelCredentials) -> None:
        client, poster = make_ap_client(ok())

        async with client:
            await client.choose_server(credentials, proxy_server="proxy.test")

        assert poster.urls == [f"https://proxy.test/ap/?url=ap-1.test{AP_PATH}?v=2"]

    async def test_default_hosts_are_the_sdk_hosts_in_order(self, credentials: ChannelCredentials) -> None:
        client = AgoraAPClient(clock=lambda: FIXED_NOW, id_factory=_ids(SID, OPID))
        poster = RecordedPoster(*(aiohttp.ClientError() for _ in AP_HOSTS))
        client._post = poster

        with pytest.raises(APError):
            await client.choose_server(credentials)

        assert poster.urls == [_url(host) for host in AP_HOSTS]


class TestHostFallback:
    @pytest.mark.parametrize(
        "failure",
        [aiohttp.ClientError(), TimeoutError(), (500, None), (200, None), (200, [1, 2])],
        ids=["client-error", "timeout", "non-200", "non-json", "json-not-an-object"],
    )
    async def test_a_failed_host_moves_on_to_the_next(self, credentials: ChannelCredentials, failure: Outcome) -> None:
        client, poster = make_ap_client(failure, ok())

        async with client:
            response = await client.choose_server(credentials)

        assert poster.urls == [_url(HOSTS[0]), _url(HOSTS[1])]
        assert response.primary_flag == 4096

    async def test_raises_ap_error_with_the_last_status_after_every_host(self, credentials: ChannelCredentials) -> None:
        client, poster = make_ap_client(aiohttp.ClientError(), TimeoutError(), (503, None))

        async with client:
            with pytest.raises(APError) as excinfo:
                await client.choose_server(credentials)

        assert poster.urls == [_url(h) for h in HOSTS]
        assert excinfo.value.status == 503

    async def test_the_final_error_has_no_status_when_the_last_host_raised(
        self, credentials: ChannelCredentials
    ) -> None:
        client, _ = make_ap_client((503, None), (502, None), TimeoutError())

        async with client:
            with pytest.raises(APError) as excinfo:
                await client.choose_server(credentials)

        assert excinfo.value.status is None

    @pytest.mark.parametrize("body", [{}, {"response_body": []}, {"response_body": "none"}])
    async def test_a_200_without_response_body_moves_on_to_the_next_host(
        self, credentials: ChannelCredentials, body: dict[str, object]
    ) -> None:
        client, poster = make_ap_client((200, body), ok())

        async with client:
            response = await client.choose_server(credentials)

        assert poster.urls == [_url(HOSTS[0]), _url(HOSTS[1])]
        assert response.primary_flag == 4096

    async def test_raises_ap_error_when_every_host_answers_without_response_body(
        self, credentials: ChannelCredentials
    ) -> None:
        client, poster = make_ap_client(*((200, {}) for _ in HOSTS))

        async with client:
            with pytest.raises(APError) as excinfo:
                await client.choose_server(credentials)

        assert not isinstance(excinfo.value, APRejectedError)
        assert len(poster.urls) == len(HOSTS)
        assert excinfo.value.__context__ is None

    async def test_a_rejection_is_final_and_tries_no_other_host(self, credentials: ChannelCredentials) -> None:
        client, poster = make_ap_client(ok("choose_server_all_failed"))

        async with client:
            with pytest.raises(APRejectedError):
                await client.choose_server(credentials)

        assert poster.urls == [_url(HOSTS[0])]


class TestUpdateTicket:
    async def test_sends_uri_28_with_the_edges_and_the_gateway_service(self, credentials: ChannelCredentials) -> None:
        client, poster = make_ap_client(ok("update_ticket_response"))

        async with client:
            await client.update_ticket(credentials, EDGES)

        body = poster.bodies[0]["request_bodies"][0]
        assert body["uri"] == AP_URI_UPDATE_TICKET
        assert body["buffer"]["service_ids"] == [11]
        assert body["buffer"]["edges_services"] == [
            {"ip": "203.0.113.10", "port": 4713},
            {"ip": "203.0.113.11", "port": 4713},
        ]

    async def test_returns_the_refreshed_ticket(self, credentials: ChannelCredentials) -> None:
        client, _ = make_ap_client(ok("update_ticket_response"))

        async with client:
            response = await client.update_ticket(credentials, EDGES)

        assert response.ticket == "TICKET_UPDATED_REDACTED"


class TestTlsVerification:
    """D10: the AP call verifies TLS unless the host opts out."""

    @pytest.mark.parametrize(("kwargs", "ssl"), [({}, True), ({"verify_ssl": False}, False)], ids=["default", "off"])
    async def test_passes_the_verify_flag_to_aiohttp(
        self, credentials: ChannelCredentials, kwargs: dict[str, bool], ssl: bool
    ) -> None:
        http = RecordingHttpSession((200, load_fixture("ap/choose_server_response.json")))
        client = AgoraAPClient(http, hosts=HOSTS, clock=lambda: FIXED_NOW, id_factory=_ids(SID, OPID), **kwargs)

        await client.choose_server(credentials)

        assert [call_kwargs["ssl"] for _, call_kwargs in http.calls] == [ssl]

    async def test_reads_a_body_that_is_not_utf8_without_raising(self, credentials: ChannelCredentials) -> None:
        http = RecordingHttpSession((200, b"\xff not json"), (200, load_fixture("ap/choose_server_response.json")))
        client = AgoraAPClient(http, hosts=HOSTS, clock=lambda: FIXED_NOW, id_factory=_ids(SID, OPID))

        response = await client.choose_server(credentials)

        assert response.primary_flag == 4096
        assert len(http.calls) == 2


class TestSessionLifetime:
    async def test_does_not_close_a_borrowed_session(self) -> None:
        async with aiohttp.ClientSession() as session:
            client = AgoraAPClient(session, hosts=HOSTS)

            await client.close()

            assert client._http() is session
            assert not session.closed

    async def test_creates_no_session_until_one_is_needed(self) -> None:
        client, _ = make_ap_client()

        await client.close()

        assert client._session is None

    async def test_closes_its_own_session_on_close(self) -> None:
        client = AgoraAPClient(hosts=HOSTS)
        owned = client._http()

        await client.close()

        assert owned.closed

    async def test_closes_its_own_session_on_context_exit(self) -> None:
        async with AgoraAPClient(hosts=HOSTS) as client:
            owned = client._http()

        assert owned.closed

    async def test_memoises_its_own_session(self) -> None:
        client = AgoraAPClient(hosts=HOSTS)

        first, second = client._http(), client._http()
        await client.close()

        assert first is second

    async def test_close_is_idempotent(self) -> None:
        client = AgoraAPClient(hosts=HOSTS)
        owned = client._http()

        await client.close()
        await client.close()

        assert owned.closed


class TestRedaction:
    def test_client_repr_carries_no_token(self) -> None:
        client, _ = make_ap_client()

        assert RTC_TOKEN not in repr(client)

    async def test_the_final_error_carries_no_token(self, credentials: ChannelCredentials) -> None:
        client, _ = make_ap_client(*(aiohttp.ClientError(RTC_TOKEN) for _ in HOSTS))

        async with client:
            with pytest.raises(APError) as excinfo:
                await client.choose_server(credentials)

        assert RTC_TOKEN not in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None
