"""Fixture loaders and fixture-derived variants shared by the sdp unit tests."""

from __future__ import annotations

import copy
from typing import Any

from tests._helpers import load_fixture, load_json_fixture

MID_URI = "urn:ietf:params:rtp-hdrext:sdes:mid"


def chrome_offer() -> str:
    """The Chrome recvonly offer with the CRLF line endings a browser delivers."""
    return crlf(load_fixture("sdp/chrome_recvonly_offer.sdp"))


def chrome_answer() -> str:
    """HA-Luba's shipped answer to the Chrome offer (``docs/protocol.md`` §6.3), CRLF."""
    return crlf(load_fixture("sdp/chrome_answer_shipped.sdp"))


def go2rtc_offer() -> str:
    """The reconstructed go2rtc/pion non-trickle offer, CRLF."""
    return crlf(load_fixture("sdp/go2rtc_offer.sdp"))


def gateway_ortc(name: str = "gateway_ortc_vp8") -> dict[str, Any]:
    """A fresh copy of a gateway ORTC fixture, safe to mutate."""
    return copy.deepcopy(load_json_fixture(f"sdp/{name}.json"))


def crlf(text: str) -> str:
    """``text`` with every line ending as CRLF."""
    return "".join(f"{line}\r\n" for line in text.splitlines())


def without_lines(sdp: str, prefix: str) -> str:
    """``sdp`` minus every line starting with ``prefix``."""
    return crlf("\n".join(line for line in sdp.splitlines() if not line.startswith(prefix)))


def with_line_after(sdp: str, anchor: str, new_line: str) -> str:
    """``sdp`` with ``new_line`` inserted after the first line equal to ``anchor``."""
    lines = sdp.splitlines()
    index = lines.index(anchor)
    return crlf("\n".join([*lines[: index + 1], new_line, *lines[index + 1 :]]))


def section_lines(sdp: str, mid: str) -> list[str]:
    """The lines of the media section whose ``a=mid`` is ``mid``, its ``m=`` line first."""
    sections: list[list[str]] = []
    for line in sdp.splitlines():
        if line.startswith("m="):
            sections.append([])
        if sections:
            sections[-1].append(line)
    return next(section for section in sections if f"a=mid:{mid}" in section)
