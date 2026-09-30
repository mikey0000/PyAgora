"""ICE candidate helpers: encode for the join ORTC (D11), read inline and trickled candidates, filter for TURN.

Candidates travel as the viewer's verbatim ``candidate:`` strings (``IceCandidate``), so they are read line by
line rather than through ``sdp_transform``, which rewrites numeric foundations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyagorartc.models import IceCandidate

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

_LOGGER = logging.getLogger(__name__)

_CANDIDATE_LINE = "a=candidate:"
_CANDIDATE_PREFIX = "candidate:"
# RFC 8445 §5.1: foundation component transport priority address port "typ" type [extensions]
_MIN_FIELDS = 8
_REFLEXIVE_TYPES = frozenset({"srflx", "prflx"})
_RELAY_TYPE = "relay"


def candidates_to_ortc(candidates: Iterable[IceCandidate | str]) -> list[dict[str, str | int]]:
    """The candidates in the ``iceParameters.candidates`` shape ``join_v3`` carries.

    Each entry has ``foundation``, ``ip``, ``port``, ``priority``, ``protocol`` and ``type``, the fields both
    shipped copies sent. A candidate that does not parse is skipped with a DEBUG line.
    """
    encoded: list[dict[str, str | int]] = []
    for candidate in candidates:
        text = candidate.candidate if isinstance(candidate, IceCandidate) else candidate
        if (fields := _fields(text)) is None:
            _LOGGER.debug("Skipping unreadable ICE candidate %r", text)
            continue
        foundation, _component, protocol, priority, ip, port, _typ, kind = fields[:_MIN_FIELDS]
        encoded.append(
            {
                "foundation": foundation,
                "ip": ip,
                "port": int(port),
                "priority": int(priority),
                "protocol": protocol,
                "type": kind,
            }
        )
    return encoded


def parse_trickle_fragment(fragment: str) -> list[IceCandidate]:
    """The candidates in a WHEP ``PATCH`` body (RFC 8840 ``trickle-ice-sdpfrag``), each tagged with its ``a=mid``.

    ``sdp_mline_index`` is left unset: a fragment names sections by mid and need not list every offer m-line.
    """
    return _read_candidates(fragment, with_index=False)


def extract_inline_candidates(offer_sdp: str) -> list[IceCandidate]:
    """The candidates written into an offer (a non-trickle offer, or ones gathered before it), with mid and index."""
    return _read_candidates(offer_sdp, with_index=True)


def filter_candidates(candidates: Iterable[IceCandidate], turn_ips: Collection[str]) -> list[IceCandidate]:
    """Drop host candidates: keep srflx/prflx, and relays whose address is one of ``turn_ips`` (any relay if empty).

    Returns the input unchanged when nothing would survive, so a viewer is never left without a candidate.
    """
    candidates = list(candidates)
    kept = []
    for candidate in candidates:
        if (fields := _fields(candidate.candidate)) is None:
            continue
        address, kind = fields[4], fields[7]
        if kind in _REFLEXIVE_TYPES or (kind == _RELAY_TYPE and (not turn_ips or address in turn_ips)):
            kept.append(candidate)
    return kept or candidates


def _fields(text: str) -> list[str] | None:
    fields = text.strip().removeprefix("a=").removeprefix(_CANDIDATE_PREFIX).split()
    if len(fields) < _MIN_FIELDS or fields[6] != "typ" or not (fields[3].isdigit() and fields[5].isdigit()):
        return None
    return fields


def _read_candidates(sdp: str, *, with_index: bool) -> list[IceCandidate]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for raw in sdp.splitlines():
        line = raw.strip()
        if line.startswith("m="):
            sections.append((None, []))
        elif line.startswith("a=mid:"):
            sections[-1] = (line.removeprefix("a=mid:").strip(), sections[-1][1])
        elif line.startswith(_CANDIDATE_LINE):
            sections[-1][1].append(line.removeprefix("a="))
    return [
        IceCandidate(text, sdp_mid=mid, sdp_mline_index=index - 1 if with_index and index else None)
        for index, (mid, lines) in enumerate(sections)
        for text in lines
    ]
