"""``APResponse`` against the access-point answers captured on 2026-10-01 (``ap/real/``)."""

from __future__ import annotations

from typing import Any

import pytest

from pyagorartc.ap.password import derive_password
from pyagorartc.ap.response import APResponse, fingerprints_from_edge
from pyagorartc.exceptions import APRejectedError
from pyagorartc.models import TurnCredentialStrategy
from tests._helpers import load_json_fixture

GATEWAY = 4096
TURN = 4194310


def _captured(name: str = "choose_server_response.json") -> dict[str, Any]:
    return load_json_fixture(f"ap/real/{name}")


def _join_ap_response() -> dict[str, Any]:
    """The ``ap_response`` the library sent in the join that followed ``choose_server_response.json``."""
    return load_json_fixture("gateway/real/join_v3.json")["_message"]["ap_response"]


class TestCapturedResponse:
    @pytest.mark.parametrize("name", ["choose_server_response.json", "choose_server_response_turn_first.json"])
    def test_reads_both_blocks_with_the_gateway_primary_in_either_order(self, name: str) -> None:
        data = _captured(name)

        response = APResponse.from_api_response(data)

        assert list(response.responses) == [b["buffer"]["flag"] for b in data["response_body"]]
        assert response.primary_flag == GATEWAY

    def test_matches_each_bare_detail_19_fingerprint_to_its_edge_by_index(self) -> None:
        data = _captured()
        fingerprints = data["response_body"][0]["buffer"]["detail"]["19"].split(";")

        edges = APResponse.from_api_response(data).get_gateway_addresses()

        assert [e.fingerprint for e in edges] == fingerprints[: len(edges)]

    def test_a_bare_fingerprint_reads_as_sha_256(self) -> None:
        edge = APResponse.from_api_response(_captured()).get_gateway_addresses()[0]

        assert fingerprints_from_edge(edge) == [{"algorithm": "sha-256", "fingerprint": edge.fingerprint}]

    def test_turn_edges_carry_no_fingerprint(self) -> None:
        edges = APResponse.from_api_response(_captured()).get_turn_addresses()

        assert [(e.port, e.fingerprint) for e in edges] == [(443, None)] * 3

    def test_the_join_ap_response_is_the_one_the_live_join_carried(self) -> None:
        response = APResponse.from_api_response(_captured())

        assert response.to_ap_response() == _join_ap_response()

    def test_both_blocks_report_the_vid_as_detail_8(self) -> None:
        response = APResponse.from_api_response(_captured())

        assert {flag: block.detail["8"] for flag, block in response.responses.items()} == {
            GATEWAY: "987654",
            TURN: "987654",
        }


class TestCapturedTurnCredentials:
    @pytest.mark.regression
    def test_detail_first_never_sends_the_vid_as_the_turn_username(self) -> None:
        """``DETAIL_FIRST`` sent the TURN block's detail 8 as the TURN username, but detail 8 is the ``vid`` (D32)."""
        response = APResponse.from_api_response(_captured())

        with pytest.warns(DeprecationWarning, match="DETAIL_FIRST"):
            servers = response.get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)

        assert {(s.username, s.credential) for s in servers} == {(str(response.uid), derive_password(response.uid))}
        assert response.responses[TURN].detail["8"] not in {s.username for s in servers}


class TestCapturedRejection:
    """``choose_server_rejected_no_authorized.json``: the AP's answer to the real token with a uid it was not minted for."""

    def test_raises_rejected_with_each_service_code(self) -> None:
        with pytest.raises(APRejectedError) as excinfo:
            APResponse.from_api_response(_captured("choose_server_rejected_no_authorized.json"))

        assert excinfo.value.codes == {GATEWAY: 2010009, TURN: 2010009}

    def test_the_message_names_the_sdk_reason_once(self) -> None:
        with pytest.raises(APRejectedError) as excinfo:
            APResponse.from_api_response(_captured("choose_server_rejected_no_authorized.json"))

        assert str(excinfo.value).endswith(f"{{{GATEWAY}: 2010009, {TURN}: 2010009}} (2010009 NO_AUTHORIZED)")

    def test_a_rejected_block_carries_no_ticket_no_edges_and_only_detail_10(self) -> None:
        buffers = [b["buffer"] for b in _captured("choose_server_rejected_no_authorized.json")["response_body"]]

        assert [(b["cert"], "edges_services" in b, sorted(b["detail"])) for b in buffers] == [("", False, ["10"])] * 2
