"""``AgoraAPClient`` against the fake access points: the real multipart ``_post``, host fallback and parsing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyagorartc.ap.client import AgoraAPClient, build_request_payload
from pyagorartc.ap.password import derive_password
from pyagorartc.const import (
    AP_FLAG_GATEWAY,
    AP_URI_CHOOSE_SERVER,
    AP_URI_UPDATE_TICKET,
    DEFAULT_SERVICE_IDS,
    EDGE_DOMAIN_SUFFIX,
    SERVICE_GATEWAY,
    TURN_PORT,
    TURNS_PORT,
)
from pyagorartc.exceptions import APError
from pyagorartc.models import TurnCredentialStrategy
from tests._helpers import CREDENTIALS, SECRET_VALUES, TICKET
from tests.fakegateway._common import (
    CID,
    LOOPBACK,
    TURN_IPS,
    TURN_PASSWORD,
    TURN_PORT as FAKE_TURN_EDGE_PORT,
    TURN_USERNAME,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import aiohttp

    from pyagorartc.ap.response import APResponse
    from tests.fakegateway import FakeAgora
    from tests.unit._fakes import ManualClock

TIMEOUT_S = 5.0
SID = 152075528
OPID = 999972753885
# Port 1 on loopback has no listener, so the connect is refused: the ClientError branch, not an HTTP status.
UNREACHABLE_HOST = f"http://{LOOPBACK}:1"


def _ids(*values: int) -> Callable[[], int]:
    return iter(values).__next__


async def _choose(client: AgoraAPClient) -> APResponse:
    return await asyncio.wait_for(client.choose_server(CREDENTIALS), timeout=TIMEOUT_S)


def _turn_urls(ip: str) -> list[str]:
    return [
        f"turn:{ip}:{TURN_PORT}?transport=udp",
        f"turn:{ip}:{TURN_PORT}?transport=tcp",
        f"turns:{ip.replace('.', '-')}{EDGE_DOMAIN_SUFFIX}:{TURNS_PORT}?transport=tcp",
    ]


class TestChooseServer:
    async def test_gateway_edges_point_at_the_fake_gateway(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        edges = [(e.ip, e.port) for e in response.get_gateway_addresses()]
        assert edges == [(fake_agora.state.gateway_host, fake_agora.state.gateway_port)]
        assert response.primary_flag == AP_FLAG_GATEWAY

    async def test_turn_edges_are_the_fakes_three_documentation_addresses(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        assert [(e.ip, e.port) for e in response.get_turn_addresses()] == [(ip, FAKE_TURN_EDGE_PORT) for ip in TURN_IPS]

    async def test_ticket_uid_and_cid_come_from_the_gateway_block(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        assert (response.ticket, response.uid, response.cid) == (TICKET, CREDENTIALS.uid, CID)
        assert response.primary is response.responses[AP_FLAG_GATEWAY]

    async def test_ice_servers_are_the_udp_tcp_turns_triple_with_uid_credentials(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        servers = (await _choose(client)).get_ice_servers()

        assert [s.urls for s in servers] == [[url] for url in _turn_urls(TURN_IPS[0])]
        assert {(s.username, s.credential) for s in servers} == {
            (str(CREDENTIALS.uid), derive_password(CREDENTIALS.uid))
        }

    async def test_detail_first_ice_servers_use_the_turn_blocks_detail_credentials(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        servers = (await _choose(client)).get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)

        assert {(s.username, s.credential) for s in servers} == {(TURN_USERNAME, TURN_PASSWORD)}

    async def test_sends_the_payload_build_request_payload_builds_for_the_same_clock_and_ids(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, fake_clock: ManualClock
    ) -> None:
        client = AgoraAPClient(
            session=raw_http, hosts=fake_agora.ap_hosts, clock=fake_clock, id_factory=_ids(SID, OPID)
        )

        await _choose(client)

        expected = build_request_payload(
            CREDENTIALS,
            uri=AP_URI_CHOOSE_SERVER,
            service_ids=DEFAULT_SERVICE_IDS,
            sid=str(SID),
            opid=OPID,
            client_ts=int(fake_clock() * 1000),
        )
        assert fake_agora.state.log.ap_requests[-1].envelope == expected


class TestHostFallback:
    async def test_the_third_host_answers_after_two_fail(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(ap_fail_hosts=2)
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        log = fake_agora.state.log.ap_requests
        assert [(r.host_index, r.status) for r in log] == [(0, 503), (1, 503), (2, 200)]
        assert response.ticket == TICKET

    async def test_raises_ap_error_with_the_last_status_when_every_host_fails(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(ap_fail_hosts=len(fake_agora.ap_hosts))
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        with pytest.raises(APError) as caught:
            await _choose(client)

        assert caught.value.status == 503
        assert [r.status for r in fake_agora.state.log.ap_requests] == [503] * len(fake_agora.ap_hosts)

    async def test_an_unreachable_host_moves_to_the_next(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=[UNREACHABLE_HOST, *fake_agora.ap_hosts])

        response = await _choose(client)

        assert [(r.host_index, r.status) for r in fake_agora.state.log.ap_requests] == [(0, 200)]
        assert response.ticket == TICKET


class TestPartialAndReorderedAnswers:
    async def test_a_failed_turn_block_is_skipped_and_the_gateway_stays_primary(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(ap_turn_code=5)
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        assert list(response.responses) == [AP_FLAG_GATEWAY]
        assert response.get_turn_addresses() == []

    async def test_ice_servers_fall_back_to_the_gateway_edge_without_turn_edges(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(ap_turn_code=5)
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        servers = (await _choose(client)).get_ice_servers()

        assert [s.urls for s in servers] == [[url] for url in _turn_urls(LOOPBACK)]

    async def test_the_gateway_block_is_primary_even_when_it_arrives_second(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        fake_agora.control(envelope_flag_order=("turn", "gateway"))
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        assert next(iter(response.responses)) != AP_FLAG_GATEWAY
        assert response.primary_flag == AP_FLAG_GATEWAY


class TestUpdateTicket:
    async def test_round_trips_with_uri_28_and_the_known_edges(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)
        edges = (await _choose(client)).get_gateway_addresses()

        response = await asyncio.wait_for(client.update_ticket(CREDENTIALS, edges), timeout=TIMEOUT_S)

        body = fake_agora.state.log.ap_requests[-1].envelope["request_bodies"][0]
        assert body["uri"] == AP_URI_UPDATE_TICKET
        assert body["buffer"]["service_ids"] == [SERVICE_GATEWAY]
        assert body["buffer"]["edges_services"] == [{"ip": e.ip, "port": e.port} for e in edges]
        assert (response.primary_flag, response.ticket) == (AP_FLAG_GATEWAY, TICKET)


class TestSessionOwnership:
    async def test_an_owned_session_is_created_and_closed_by_the_context_manager(self, fake_agora: FakeAgora) -> None:
        async with AgoraAPClient(hosts=fake_agora.ap_hosts) as client:
            response = await _choose(client)
            owned = client._http()

        assert response.ticket == TICKET
        assert owned.closed

    async def test_a_borrowed_session_stays_open_and_usable(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        async with AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts) as client:
            await _choose(client)

        await _choose(AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts))

        assert not raw_http.closed
        assert [r.status for r in fake_agora.state.log.ap_requests] == [200, 200]

    async def test_accepts_verify_ssl_false_on_the_post_path(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        # The fake serves plain http, where aiohttp ignores ``ssl=``; this pins that the flag is accepted
        # on the real post path, not that certificate checks are skipped (that needs a TLS listener).
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts, verify_ssl=False)

        await _choose(client)

        assert [r.status for r in fake_agora.state.log.ap_requests] == [200]


class TestSecrets:
    async def test_the_parsed_response_repr_carries_no_secret(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        client = AgoraAPClient(session=raw_http, hosts=fake_agora.ap_hosts)

        response = await _choose(client)

        text = repr(response) + repr(response.primary) + repr(response.get_ice_servers()) + repr(client)
        secrets = (*SECRET_VALUES, TURN_PASSWORD, derive_password(CREDENTIALS.uid))
        assert [s for s in secrets if s in text] == []
