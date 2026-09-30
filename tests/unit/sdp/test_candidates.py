from __future__ import annotations

import pytest

from pyagorartc.models import IceCandidate
from pyagorartc.sdp.candidates import (
    candidates_to_ortc,
    extract_inline_candidates,
    filter_candidates,
    parse_trickle_fragment,
)
from tests._helpers import load_fixture
from tests.unit.sdp._helpers import chrome_offer, go2rtc_offer, with_line_after, without_lines

TURN_IPS = frozenset({"198.51.100.30"})


def _fragment() -> list[IceCandidate]:
    return parse_trickle_fragment(load_fixture("sdp/whep_trickle_fragment.sdpfrag"))


def _types(candidates: list[IceCandidate]) -> list[str]:
    return [c.candidate.split()[7] for c in candidates]


class TestCandidatesToOrtc:
    def test_encodes_a_viewer_candidate_in_the_join_shape(self) -> None:
        ortc = candidates_to_ortc(extract_inline_candidates(chrome_offer()))

        assert ortc == [
            {
                "foundation": "3425633422",
                "ip": "192.0.2.50",
                "port": 54321,
                "priority": 2122260223,
                "protocol": "udp",
                "type": "host",
            }
        ]

    def test_accepts_bare_strings_with_or_without_the_candidate_prefix(self) -> None:
        line = extract_inline_candidates(chrome_offer())[0].candidate

        ortc = candidates_to_ortc([line, line.removeprefix("candidate:"), f"a={line}"])

        assert len(ortc) == 3
        assert ortc[0] == ortc[1] == ortc[2]

    def test_keeps_the_related_address_out_of_the_join_shape(self) -> None:
        srflx = [c for c in _fragment() if " typ srflx " in c.candidate]

        ortc = candidates_to_ortc(srflx)

        assert ortc == [
            {
                "foundation": "2",
                "ip": "198.51.100.20",
                "port": 54321,
                "priority": 1686052607,
                "protocol": "udp",
                "type": "srflx",
            }
        ]

    @pytest.mark.parametrize(
        "malformed",
        [
            "",
            "candidate:",
            "candidate:1 1 udp 2122260223 192.0.2.50 54321",
            "candidate:1 1 udp high 192.0.2.50 54321 typ host",
            "candidate:1 1 udp 2122260223 192.0.2.50 port typ host",
            "candidate:1 1 udp 2122260223 192.0.2.50 54321 kind host",
        ],
    )
    def test_skips_a_candidate_it_cannot_read(self, malformed: str) -> None:
        good = extract_inline_candidates(chrome_offer())[0]

        ortc = candidates_to_ortc([IceCandidate(malformed), good])

        assert [c["ip"] for c in ortc] == ["192.0.2.50"]


class TestParseTrickleFragment:
    def test_reads_every_candidate_verbatim_with_its_mid(self) -> None:
        candidates = _fragment()

        assert [(c.sdp_mid, c.candidate.split()[0]) for c in candidates] == [
            ("0", "candidate:1"),
            ("0", "candidate:2"),
            ("0", "candidate:3"),
            ("0", "candidate:4"),
            ("0", "candidate:5"),
            ("1", "candidate:6"),
        ]
        assert candidates[1].candidate == (
            "candidate:2 1 udp 1686052607 198.51.100.20 54321 typ srflx raddr 192.0.2.50 rport 54321 generation 0"
        )

    def test_leaves_the_mline_index_unset(self) -> None:
        """RFC 8840 fragments identify sections by mid; their m-line order need not match the offer."""
        assert {c.sdp_mline_index for c in _fragment()} == {None}

    def test_gives_a_candidate_before_any_media_section_no_mid(self) -> None:
        fragment = load_fixture("sdp/whep_trickle_fragment.sdpfrag")
        first = next(line for line in fragment.splitlines() if line.startswith("a=candidate:"))

        candidates = parse_trickle_fragment(f"{first}\r\n")

        assert candidates == [IceCandidate(first.removeprefix("a="), sdp_mid=None, sdp_mline_index=None)]

    def test_returns_nothing_for_a_fragment_without_candidates(self) -> None:
        fragment = without_lines(load_fixture("sdp/whep_trickle_fragment.sdpfrag"), "a=candidate:")

        assert parse_trickle_fragment(fragment) == []


class TestFilterCandidates:
    def test_keeps_reflexive_candidates_and_relays_on_a_turn_address(self) -> None:
        kept = filter_candidates(_fragment(), TURN_IPS)

        assert [(t, c.candidate.split()[4]) for t, c in zip(_types(kept), kept, strict=True)] == [
            ("srflx", "198.51.100.20"),
            ("prflx", "198.51.100.21"),
            ("relay", "198.51.100.30"),
        ]

    def test_keeps_every_relay_when_no_turn_address_is_known(self) -> None:
        kept = filter_candidates(_fragment(), frozenset())

        assert _types(kept) == ["srflx", "prflx", "relay", "relay"]

    def test_returns_the_input_when_nothing_would_survive(self) -> None:
        hosts = [c for c in _fragment() if " typ host " in c.candidate]

        assert filter_candidates(hosts, TURN_IPS) == hosts

    def test_drops_a_candidate_it_cannot_read(self) -> None:
        kept = filter_candidates([IceCandidate("candidate:garbled"), *_fragment()], TURN_IPS)

        assert _types(kept) == ["srflx", "prflx", "relay"]

    def test_matches_the_relay_address_not_its_related_address(self) -> None:
        kept = filter_candidates(_fragment(), frozenset({"198.51.100.20"}))

        assert _types(kept) == ["srflx", "prflx"]


class TestExtractInlineCandidates:
    def test_reads_the_inline_candidates_of_a_non_trickle_offer(self) -> None:
        candidates = extract_inline_candidates(go2rtc_offer())

        assert [(c.sdp_mid, c.sdp_mline_index) for c in candidates] == [("0", 0)] * 4
        assert candidates[2].candidate == (
            "candidate:2786424364 1 udp 1694498815 198.51.100.20 50000 typ srflx raddr 0.0.0.0 rport 50000"
        )

    def test_reads_a_trickle_candidate_the_browser_had_before_the_offer(self) -> None:
        candidates = extract_inline_candidates(chrome_offer())

        assert [(c.sdp_mid, c.sdp_mline_index) for c in candidates] == [("0", 0)]

    def test_indexes_a_candidate_by_the_mline_it_sits_under(self) -> None:
        line = extract_inline_candidates(chrome_offer())[0].candidate
        offer = with_line_after(chrome_offer(), "a=mid:1", f"a={line}")

        candidates = extract_inline_candidates(offer)

        assert [(c.sdp_mid, c.sdp_mline_index) for c in candidates] == [("0", 0), ("1", 1)]

    def test_returns_nothing_for_an_offer_without_candidates(self) -> None:
        assert extract_inline_candidates(without_lines(chrome_offer(), "a=candidate:")) == []
