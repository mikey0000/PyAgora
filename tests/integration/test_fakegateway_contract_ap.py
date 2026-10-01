"""The fake's access-point route answers the shapes protocol.md §1 records."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._helpers import CREDENTIALS, load_json_fixture
from tests.fakegateway._common import LOOPBACK, edge_fingerprint
from tests.integration._helpers import AP_PATH, ap_envelope, ap_form

if TYPE_CHECKING:
    import aiohttp

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import Frame


async def _post(raw_http: aiohttp.ClientSession, host: str, envelope: Frame | str) -> tuple[int, Frame]:
    async with raw_http.post(f"{host}{AP_PATH}", data=ap_form(envelope)) as resp:
        return resp.status, await resp.json(content_type=None)


def _block(response: Frame, flag: int) -> Frame:
    return next(b["buffer"] for b in response["response_body"] if b["buffer"]["flag"] == flag)


class TestEnvelopeValidation:
    async def test_rejects_a_body_that_is_not_multipart(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        async with raw_http.post(f"{fake_agora.ap_hosts[0]}{AP_PATH}", json=ap_envelope()) as resp:
            status = resp.status

        assert status == 400
        assert "multipart" in (fake_agora.state.log.ap_requests[0].error or "")

    async def test_rejects_a_request_field_that_is_not_json(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        status, body = await _post(raw_http, fake_agora.ap_hosts[0], "not json")

        assert status == 400
        assert "not a JSON object" in body["error"]

    @pytest.mark.parametrize("key", ["appid", "client_ts", "opid", "sid", "request_bodies"])
    async def test_rejects_an_envelope_missing_a_required_key(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, key: str
    ) -> None:
        envelope = ap_envelope()
        del envelope[key]

        status, body = await _post(raw_http, fake_agora.ap_hosts[0], envelope)

        assert status == 400
        assert repr(key) in body["error"]

    async def test_rejects_a_sid_sent_as_a_number(self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession) -> None:
        status, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope(sid=152075528))

        assert status == 400
        assert "sid" in body["error"]

    async def test_rejects_a_uri_other_than_choose_server_or_update_ticket(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        envelope = ap_envelope()
        envelope["request_bodies"][0]["uri"] = 23

        status, body = await _post(raw_http, fake_agora.ap_hosts[0], envelope)

        assert status == 400
        assert "uri 23" in body["error"]

    async def test_rejects_response_flags_sent_as_service_ids(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        status, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope(service_ids=(4096,)))

        assert status == 400
        assert "4096" in body["error"]

    async def test_rejects_an_update_ticket_without_edges(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        envelope = ap_envelope(uri=28)
        del envelope["request_bodies"][0]["buffer"]["edges_services"]

        status, body = await _post(raw_http, fake_agora.ap_hosts[0], envelope)

        assert status == 400
        assert "edges_services" in body["error"]

    @pytest.mark.parametrize("key", ["cname", "detail", "key", "service_ids", "uid"])
    async def test_rejects_a_buffer_missing_a_required_key(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession, key: str
    ) -> None:
        envelope = ap_envelope()
        del envelope["request_bodies"][0]["buffer"][key]

        status, body = await _post(raw_http, fake_agora.ap_hosts[0], envelope)

        assert status == 400
        assert repr(key) in body["error"]

    async def test_records_the_parsed_envelope_and_host(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        await _post(raw_http, fake_agora.ap_hosts[2], ap_envelope())

        record = fake_agora.state.log.ap_requests[0]
        assert (record.host_index, record.status) == (2, 200)
        assert record.envelope == ap_envelope()


class TestChooseServerResponse:
    async def test_echoes_the_opid_and_answers_one_block_per_service(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        status, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope())

        assert status == 200
        assert body["opid"] == 999972753885
        assert [b["buffer"]["flag"] for b in body["response_body"]] == [4096, 4194310]
        assert {b["uri"] for b in body["response_body"]} == {23}

    async def test_gateway_block_points_at_the_fake_gateway(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        _, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope())

        gateway = _block(body, 4096)
        assert gateway["code"] == 0
        assert gateway["edges_services"] == [{"ip": "127.0.0.1", "port": fake_agora.state.gateway_port}]
        assert (gateway["uid"], gateway["cname"], gateway["cert"]) == (
            CREDENTIALS.uid,
            CREDENTIALS.channel_name,
            "ticket-not-real",
        )

    async def test_gateway_block_carries_one_fingerprint_per_edge(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        _, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope())

        gateway = _block(body, 4096)
        edges = gateway["edges_services"]
        assert gateway["detail"]["19"] == "".join(f"{edge_fingerprint(e['ip'], e['port'])};" for e in edges)

    async def test_blocks_and_top_level_carry_the_captured_detail_keys(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        real = load_json_fixture("ap/real/choose_server_response.json")

        _, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope())

        assert sorted(body) == sorted(real)
        assert (body["detail"], body["wan_ip"]) == ({"502": LOOPBACK}, LOOPBACK)
        for flag in (4096, 4194310):
            assert sorted(_block(body, flag)["detail"]) == sorted(_block(real, flag)["detail"])

    async def test_turn_block_carries_test_net_edges_and_detail_credentials(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        _, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope())

        turn = _block(body, 4194310)
        assert turn["code"] == 0
        assert [e["ip"] for e in turn["edges_services"]] == ["203.0.113.10", "203.0.113.11", "203.0.113.12"]
        assert {e["port"] for e in turn["edges_services"]} == {443}
        assert (turn["detail"]["8"], turn["detail"]["4"]) == (str(fake_agora.state.vid), "turn-pass-not-real")

    async def test_answers_only_the_requested_services(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        _, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope(service_ids=(11,)))

        assert [b["buffer"]["flag"] for b in body["response_body"]] == [4096]

    async def test_assigns_the_viewer_uid_when_the_request_sends_zero(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        envelope = ap_envelope()
        envelope["request_bodies"][0]["buffer"]["uid"] = 0

        _, body = await _post(raw_http, fake_agora.ap_hosts[0], envelope)

        assert _block(body, 4096)["uid"] == fake_agora.state.viewer_uid

    async def test_answers_update_ticket_with_the_same_blocks(
        self, fake_agora: FakeAgora, raw_http: aiohttp.ClientSession
    ) -> None:
        status, body = await _post(raw_http, fake_agora.ap_hosts[0], ap_envelope(uri=28))

        assert status == 200
        assert [(b["uri"], b["buffer"]["flag"]) for b in body["response_body"]] == [(29, 4096), (29, 4194310)]
