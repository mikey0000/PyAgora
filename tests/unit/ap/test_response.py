"""``APResponse`` parsing, edge selection, ICE servers and ``fingerprints_from_edge`` (protocol.md §1.3-§1.4)."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest

from pyagorartc.ap.password import derive_password
from pyagorartc.ap.response import APResponse, fingerprints_from_edge
from pyagorartc.exceptions import APError, APRejectedError
from pyagorartc.models import EdgeAddress, TurnCredentialStrategy, TurnMode
from tests._helpers import RTC_TOKEN, load_json_fixture

if TYPE_CHECKING:
    from pyagorartc.models import ICEServer

GATEWAY = 4096
TURN = 4194310
FIXTURE_UID = 12345678
UID_PASSWORD = derive_password(FIXTURE_UID)


def _response(name: str = "choose_server_response.json") -> dict[str, Any]:
    return load_json_fixture(f"ap/{name}")


def _parse(name: str = "choose_server_response.json") -> APResponse:
    return APResponse.from_api_response(_response(name))


type Shape = tuple[tuple[str, ...], str | None, str | None]


def _elide_domain(url: str) -> str:
    """``turns:a-b-c-d.<domain>:443?…`` with the edge domain replaced, so no test spells a real host."""
    if not url.startswith("turns:"):
        return url
    host, _, rest = url.removeprefix("turns:").partition(":")
    return f"turns:{host.split('.', 1)[0]}.EDGE:{rest}"


def _shape(servers: list[ICEServer]) -> list[Shape]:
    return [(tuple(_elide_domain(u) for u in s.urls), s.username, s.credential) for s in servers]


def _turn_triple(ip: str, username: str, credential: str) -> list[Shape]:
    return [
        ((f"turn:{ip}:3478?transport=udp",), username, credential),
        ((f"turn:{ip}:3478?transport=tcp",), username, credential),
        ((f"turns:{ip.replace('.', '-')}.EDGE:443?transport=tcp",), username, credential),
    ]


class TestPrimaryBlock:
    def test_gateway_block_is_primary_when_it_comes_first(self) -> None:
        response = _parse()

        assert response.primary_flag == GATEWAY
        assert response.ticket == "TICKET_REDACTED"

    def test_first_successful_block_is_primary_when_the_gateway_failed(self) -> None:
        response = _parse("choose_server_gateway_failed.json")

        assert response.primary_flag == TURN

    def test_every_primary_scalar_comes_from_the_gateway_block_when_turn_comes_first(self) -> None:
        data = _response()
        data["response_body"].reverse()

        response = APResponse.from_api_response(data)

        assert response.primary_flag == GATEWAY
        assert (response.ticket, response.cid, response.detail["19"]) == (
            "TICKET_REDACTED",
            123456789,
            "FP_A;FP_B;FP_C",
        )
        assert [a.ip for a in response.addresses] == ["203.0.113.10", "203.0.113.11", "203.0.113.12"]

    def test_scalars_follow_the_turn_block_when_it_is_primary(self) -> None:
        response = _parse("choose_server_gateway_failed.json")

        assert (response.ticket, response.cid) == ("TURN_TICKET_REDACTED", 987654321)
        assert [a.port for a in response.addresses] == [443, 443, 443]


class TestFailedBuffers:
    def test_a_failed_turn_buffer_is_skipped_and_the_gateway_survives(self) -> None:
        response = _parse("choose_server_one_failed.json")

        assert list(response.responses) == [GATEWAY]
        assert len(response.get_gateway_addresses()) == 3
        assert response.get_turn_addresses() == []

    def test_every_buffer_failing_raises_rejected_with_each_code(self) -> None:
        with pytest.raises(APRejectedError) as excinfo:
            _parse("choose_server_all_failed.json")

        assert excinfo.value.codes == {GATEWAY: 2, TURN: 5}

    def test_a_buffer_without_a_code_counts_as_failed(self) -> None:
        data = _response()
        del data["response_body"][1]["buffer"]["code"]

        assert list(APResponse.from_api_response(data).responses) == [GATEWAY]

    def test_numeric_strings_are_read_as_numbers(self) -> None:
        data = _response()
        data["response_body"][0]["buffer"].update(code="0", cid="42")

        assert APResponse.from_api_response(data).cid == 42

    def test_a_missing_response_body_raises_ap_error_not_rejected(self) -> None:
        data = _response()
        del data["response_body"]

        with pytest.raises(APError) as excinfo:
            APResponse.from_api_response(data)

        assert not isinstance(excinfo.value, APRejectedError)

    def test_an_empty_response_body_raises_ap_error(self) -> None:
        data = _response()
        data["response_body"] = []

        with pytest.raises(APError):
            APResponse.from_api_response(data)


class TestEdges:
    def test_edges_without_ip_or_port_are_dropped(self) -> None:
        response = _parse("choose_server_irregular.json")

        assert [(a.ip, a.port) for a in response.get_gateway_addresses()] == [
            ("203.0.113.10", 4713),
            ("203.0.113.13", 4713),
        ]

    def test_fingerprints_match_edges_by_their_original_index(self) -> None:
        response = _parse("choose_server_irregular.json")

        assert [a.fingerprint for a in response.get_gateway_addresses()] == ["FP_A", "FP_D"]

    def test_every_edge_carries_its_block_ticket(self) -> None:
        response = _parse()

        assert {a.ticket for a in response.get_gateway_addresses()} == {"TICKET_REDACTED"}
        assert {a.ticket for a in response.get_turn_addresses()} == {"TURN_TICKET_REDACTED"}

    def test_edges_carry_no_turn_credentials_of_their_own(self) -> None:
        response = _parse()

        assert all(a.username is None and a.credentials is None for a in response.get_turn_addresses())

    def test_edges_without_a_fingerprint_detail_have_none(self) -> None:
        response = _parse()

        assert {a.fingerprint for a in response.get_turn_addresses()} == {None}


class TestFingerprintsFromEdge:
    def test_reads_an_algorithm_value_pair(self) -> None:
        edge = EdgeAddress(ip="203.0.113.10", port=4713, fingerprint="sha-384 C1:D2")

        assert fingerprints_from_edge(edge) == [{"algorithm": "sha-384", "fingerprint": "C1:D2"}]

    def test_reads_a_bare_value_as_sha_256(self) -> None:
        edge = EdgeAddress(ip="203.0.113.10", port=4713, fingerprint="C1:D2")

        assert fingerprints_from_edge(edge) == [{"algorithm": "sha-256", "fingerprint": "C1:D2"}]

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_is_empty_for_an_edge_without_one(self, value: str | None) -> None:
        assert fingerprints_from_edge(EdgeAddress(ip="203.0.113.10", port=4713, fingerprint=value)) == []

    def test_is_empty_for_a_value_of_more_than_two_words(self) -> None:
        edge = EdgeAddress(ip="203.0.113.10", port=4713, fingerprint="sha-256 C1:D2 extra")

        assert fingerprints_from_edge(edge) == []

    def test_reads_the_parsed_detail_19_of_each_edge(self) -> None:
        response = _parse()

        assert fingerprints_from_edge(response.get_gateway_addresses()[1]) == [
            {"algorithm": "sha-256", "fingerprint": "FP_B"}
        ]


class TestResponses:
    def test_is_set_for_a_single_block_response(self) -> None:
        response = _parse("update_ticket_response.json")

        assert list(response.responses) == [GATEWAY]
        assert response.ticket == "TICKET_UPDATED_REDACTED"

    def test_holds_every_successful_block_by_flag(self) -> None:
        response = _parse()

        assert list(response.responses) == [GATEWAY, TURN]

    def test_top_level_detail_is_merged_under_each_block_detail(self) -> None:
        response = _parse("choose_server_irregular.json")

        assert response.detail["38"] == "CROSS_REGION"
        assert response.detail["23"] == "EU"

    def test_server_ts_is_the_response_enter_ts(self) -> None:
        assert _parse().server_ts == 1790716795180

    def test_server_ts_falls_back_to_the_clock_in_milliseconds(self) -> None:
        response = APResponse.from_api_response(
            _response("choose_server_irregular.json"), clock=lambda: 1_700_000_000.5
        )

        assert response.server_ts == 1_700_000_000_500

    def test_opid_is_the_response_opid(self) -> None:
        assert _parse().opid == 999972753885


class TestAddresses:
    def test_gateway_addresses_are_the_flag_4096_edges_in_order(self) -> None:
        assert [a.ip for a in _parse().get_gateway_addresses()] == ["203.0.113.10", "203.0.113.11", "203.0.113.12"]

    def test_turn_addresses_are_the_flag_4194310_edges_in_order(self) -> None:
        assert [a.ip for a in _parse().get_turn_addresses()] == ["198.51.100.20", "198.51.100.21", "198.51.100.22"]

    def test_gateway_addresses_are_empty_without_a_gateway_block(self) -> None:
        assert _parse("choose_server_gateway_failed.json").get_gateway_addresses() == []


class TestGetIceServers:
    def test_default_is_the_first_turn_edge_in_all_three_transports_with_uid_credentials(self) -> None:
        servers = _parse().get_ice_servers()

        assert _shape(servers) == _turn_triple("198.51.100.20", str(FIXTURE_UID), UID_PASSWORD)

    def test_use_all_turn_servers_expands_every_turn_edge(self) -> None:
        servers = _parse().get_ice_servers(use_all_turn_servers=True)

        assert _shape(servers) == [
            *_turn_triple("198.51.100.20", str(FIXTURE_UID), UID_PASSWORD),
            *_turn_triple("198.51.100.21", str(FIXTURE_UID), UID_PASSWORD),
            *_turn_triple("198.51.100.22", str(FIXTURE_UID), UID_PASSWORD),
        ]

    @pytest.mark.parametrize(
        ("mode", "expected_indexes"),
        [(TurnMode.ALL, [0, 1, 2]), (TurnMode.UDP_ONLY, [0]), (TurnMode.TCP_ONLY, [1]), (TurnMode.TLS_ONLY, [2])],
    )
    def test_turn_mode_selects_the_transports(self, mode: TurnMode, expected_indexes: list[int]) -> None:
        triple = _turn_triple("198.51.100.20", str(FIXTURE_UID), UID_PASSWORD)

        servers = _parse().get_ice_servers(turn_mode=mode)

        assert _shape(servers) == [triple[i] for i in expected_indexes]

    def test_uid_strategy_ignores_the_ap_detail_credentials(self) -> None:
        servers = _parse().get_ice_servers(strategy=TurnCredentialStrategy.UID)

        assert {(s.username, s.credential) for s in servers} == {(str(FIXTURE_UID), UID_PASSWORD)}

    def test_uid_argument_overrides_the_response_uid(self) -> None:
        servers = _parse().get_ice_servers(uid=42)

        assert {(s.username, s.credential) for s in servers} == {("42", derive_password(42))}

    def test_detail_first_uses_ap_detail_8_and_4(self) -> None:
        servers = _parse().get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)

        assert {(s.username, s.credential) for s in servers} == {("TURN_USER_REDACTED", "TURN_CRED_REDACTED")}

    def test_detail_first_falls_back_to_uid_credentials_per_missing_key(self) -> None:
        data = _response()
        del data["response_body"][1]["buffer"]["detail"]["4"]

        servers = APResponse.from_api_response(data).get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)

        assert {(s.username, s.credential) for s in servers} == {("TURN_USER_REDACTED", UID_PASSWORD)}

    def test_without_a_turn_block_the_primary_edges_are_used(self) -> None:
        servers = _parse("choose_server_one_failed.json").get_ice_servers()

        assert _shape(servers) == _turn_triple("203.0.113.10", str(FIXTURE_UID), UID_PASSWORD)

    def test_a_turn_block_with_no_edges_falls_back_to_the_primary_edges(self) -> None:
        data = _response()
        data["response_body"][1]["buffer"]["edges_services"] = []

        servers = APResponse.from_api_response(data).get_ice_servers()

        assert _shape(servers) == _turn_triple("203.0.113.10", str(FIXTURE_UID), UID_PASSWORD)

    @pytest.mark.regression
    def test_detail_first_never_reads_credentials_from_the_gateway_fallback(self) -> None:
        """With no TURN edges, DETAIL_FIRST sent the gateway block's detail 8 (its ``vid``) as the TURN username."""
        data = _response()
        data["response_body"][1]["buffer"]["edges_services"] = []

        servers = APResponse.from_api_response(data).get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)

        assert {(s.username, s.credential) for s in servers} == {(str(FIXTURE_UID), UID_PASSWORD)}

    def test_detail_first_with_a_uid_override_falls_back_to_that_uid(self) -> None:
        data = _response()
        del data["response_body"][1]["buffer"]["detail"]["8"]

        servers = APResponse.from_api_response(data).get_ice_servers(
            strategy=TurnCredentialStrategy.DETAIL_FIRST, uid=42
        )

        assert {(s.username, s.credential) for s in servers} == {("42", "TURN_CRED_REDACTED")}

    def test_is_empty_when_no_block_has_edges(self) -> None:
        data = _response("choose_server_one_failed.json")
        data["response_body"][0]["buffer"]["edges_services"] = []

        assert APResponse.from_api_response(data).get_ice_servers() == []


class TestTurnServerConfig:
    def test_servers_are_the_turn_edges_with_uid_credentials_and_security(self) -> None:
        config = _parse().turn_server_config(None, None)

        assert config["mode"] == "manual"
        assert config["servers"][0] == {
            "turnServerURL": "198.51.100.20",
            "tcpport": 443,
            "udpport": 443,
            "username": str(FIXTURE_UID),
            "password": UID_PASSWORD,
            "forceturn": False,
            "security": True,
        }
        assert len(config["servers"]) == 3

    def test_servers_from_gateway_use_port_plus_30_and_the_token_as_password(self) -> None:
        gateway = EdgeAddress(ip="203.0.113.10", port=4713)

        config = _parse().turn_server_config(gateway, RTC_TOKEN)

        assert config["serversFromGateway"] == [
            {
                "username": str(FIXTURE_UID),
                "password": RTC_TOKEN,
                "turnServerURL": "203.0.113.10",
                "tcpport": 4743,
                "udpport": 4743,
                "forceturn": False,
            }
        ]

    @pytest.mark.parametrize(
        ("gateway", "token"), [(None, RTC_TOKEN), (EdgeAddress(ip="203.0.113.10", port=4713), None)]
    )
    def test_servers_from_gateway_is_empty_without_both_gateway_and_token(
        self, gateway: EdgeAddress | None, token: str | None
    ) -> None:
        assert _parse().turn_server_config(gateway, token)["serversFromGateway"] == []


class TestToApResponse:
    def test_primary_block_in_the_join_ap_response_shape(self) -> None:
        response = _parse()

        assert response.to_ap_response() == {
            "code": 0,
            "server_ts": 1790716795180,
            "uid": FIXTURE_UID,
            "cid": 123456789,
            "cname": "IOT_ID_REDACTED",
            "detail": _response()["response_body"][0]["buffer"]["detail"],
            "flag": GATEWAY,
            "opid": 999972753885,
            "cert": "TICKET_REDACTED",
            "ticket": "TICKET_REDACTED",
        }

    def test_a_named_flag_reshapes_that_block(self) -> None:
        ap = _parse().to_ap_response(TURN)

        assert (ap["flag"], ap["cid"], ap["cert"], ap["ticket"]) == (
            TURN,
            987654321,
            "TURN_TICKET_REDACTED",
            "TURN_TICKET_REDACTED",
        )

    def test_a_flag_with_no_block_raises_ap_error(self) -> None:
        with pytest.raises(APError):
            _parse("choose_server_one_failed.json").to_ap_response(TURN)

    def test_the_result_does_not_alias_the_response_detail(self) -> None:
        response = _parse()

        response.to_ap_response()["detail"]["19"] = "changed"

        assert response.detail["19"] == "FP_A;FP_B;FP_C"


class TestRedaction:
    def test_repr_carries_no_ticket_or_detail_credential(self) -> None:
        text = repr(_parse())

        for secret in ("TICKET_REDACTED", "TURN_TICKET_REDACTED", "TURN_CRED_REDACTED"):
            assert secret not in text

    def test_block_repr_carries_no_ticket_or_detail_credential(self) -> None:
        text = repr(_parse().responses[TURN])

        assert "TURN_TICKET_REDACTED" not in text
        assert "TURN_CRED_REDACTED" not in text

    def test_ice_server_repr_carries_no_derived_password(self) -> None:
        text = repr(_parse().get_ice_servers())

        assert UID_PASSWORD not in text

    def test_parsing_does_not_mutate_the_input(self) -> None:
        data = _response()
        before = copy.deepcopy(data)

        APResponse.from_api_response(data)

        assert data == before
