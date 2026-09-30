"""Pure SDP ↔ ORTC conversion (docs/architecture.md §1)."""

from pyagora.sdp.answer import STRIPPED_EXTENSIONS, answer_from_ortc, offers_rtx, setup_for_role, validate_answer
from pyagora.sdp.candidates import (
    candidates_to_ortc,
    extract_inline_candidates,
    filter_candidates,
    parse_trickle_fragment,
)
from pyagora.sdp.offer import can_send, negotiated_caps, offer_to_ortc, parse_offer

__all__ = [
    "STRIPPED_EXTENSIONS",
    "answer_from_ortc",
    "can_send",
    "candidates_to_ortc",
    "extract_inline_candidates",
    "filter_candidates",
    "negotiated_caps",
    "offer_to_ortc",
    "offers_rtx",
    "parse_offer",
    "parse_trickle_fragment",
    "setup_for_role",
    "validate_answer",
]
