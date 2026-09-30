"""Turn a viewer's SDP offer into the ORTC capabilities ``join_v3`` carries (D3).

``sdp_transform`` is the one SDP parser. The shape and the codec rules follow the Agora Web SDK's
``getOrtc`` as HA-Luba shipped it (``docs/protocol.md`` §2.4); ``tests/fixtures/sdp/chrome_offer_ortc_shipped.json``
pins parity with that code.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any, NotRequired, TypedDict

from sdp_transform import parse as sdp_parse

from pyagorartc.const import DEFAULT_ORTC_DTLS_ROLE
from pyagorartc.exceptions import SdpError

_LOGGER = logging.getLogger(__name__)


class OrtcIceParameters(TypedDict):
    """``iceParameters`` of the client ORTC; ``candidates`` is merged in by the session (D11)."""

    iceUfrag: str
    icePwd: str
    candidates: NotRequired[list[dict[str, str | int]]]


class ClientOrtc(TypedDict):
    """The client ORTC ``offer_to_ortc`` builds, keyed by its wire names (protocol.md §2.4)."""

    iceParameters: OrtcIceParameters
    dtlsParameters: dict[str, object]
    rtpCapabilities: dict[str, dict[str, list[dict[str, object]]]]
    version: str
    cname: NotRequired[str]


#: Media kind -> the ORTC codec and extension keys for it; shared with the answer builder.
KINDS: Mapping[str, tuple[str, str]] = {
    "audio": ("audioCodecs", "audioExtensions"),
    "video": ("videoCodecs", "videoExtensions"),
}
DEFAULT_CLOCK_RATE = 90000
_BUCKETS = ("send", "recv", "sendrecv")
# First non-empty bucket wins (HA-Luba ``_negotiated_caps``; protocol.md §2.5).
_CAPABILITY_ORDER = ("sendrecv", "recv", "send")
_WILDCARD_PAYLOAD = "*"
# sdp_transform coerces numeric-looking values to int/float ("0012" -> 12, "nan" -> nan), corrupting these strings.
_VERBATIM_ATTRIBUTES = {"a=mid:": "mid", "a=ice-ufrag:": "iceUfrag", "a=ice-pwd:": "icePwd"}
# The same grammar sdp_transform applies (grammar.py), so restored values line up entry for entry.
_FMTP_LINE = re.compile(r"^a=fmtp:(\d*) ([\S| ]*)")
_SSRC_LINE = re.compile(r"^a=ssrc:(\d*) ([\w_-]*)(?::(.*))?")


def parse_offer(offer_sdp: str) -> dict[str, object]:
    """Parse an SDP offer with ``sdp_transform``, keeping string attributes it would coerce to numbers (D24).

    ``mid``, ``iceUfrag``, ``icePwd``, ``payloads``, every ``groups[].mids``, every ``fmtp[].config`` and every
    ``ssrcs[].value`` are the offer's text verbatim, so ``0012``, ``1e10``, ``nan`` and ``inf`` survive.

    Raises:
        SdpError: The offer has no media sections.
    """
    return _parse(offer_sdp)


def _parse(offer_sdp: str) -> dict[str, Any]:
    parsed: dict[str, Any] = sdp_parse(offer_sdp)
    if not parsed["media"]:
        raise SdpError("offer has no media sections")
    sections: list[dict[str, Any]] = [parsed, *parsed["media"]]
    fmtp_configs: list[list[str]] = [[] for _ in sections]
    ssrc_values: list[list[str | None]] = [[] for _ in sections]
    groups: list[dict[str, str]] = []
    index = 0
    # Walk the lines exactly as sdp_transform does (unstripped, splitlines) so section indices agree.
    for raw in offer_sdp.splitlines():
        if raw.startswith("m="):
            index += 1
            sections[index]["payloads"] = " ".join(raw.split()[3:])
        elif raw.startswith("a=group:") and index == 0:
            kind, _, mids = raw.rstrip().removeprefix("a=group:").partition(" ")
            groups.append({"type": kind, "mids": mids.strip()})
        elif match := _FMTP_LINE.match(raw):
            fmtp_configs[index].append(match[2])
        elif match := _SSRC_LINE.match(raw):
            ssrc_values[index].append(match[3])
        else:
            _restore_attribute(sections[index], raw.rstrip())
    for section, configs, values in zip(sections, fmtp_configs, ssrc_values, strict=True):
        _restore(section.get("fmtp") or [], "config", configs)
        _restore(section.get("ssrcs") or [], "value", values)
    if groups:
        parsed["groups"] = groups
    return parsed


def _restore_attribute(section: dict[str, Any], line: str) -> None:
    for prefix, key in _VERBATIM_ATTRIBUTES.items():
        if line.startswith(prefix):
            section[key] = line.removeprefix(prefix).strip()


def _restore(entries: list[dict[str, Any]], key: str, raw_values: list[str] | list[str | None]) -> None:
    if len(entries) != len(raw_values):
        _LOGGER.debug("Offer %s lines did not line up with the parser's; left as parsed", key)
        return
    for entry, value in zip(entries, raw_values, strict=True):
        if value is not None:
            entry[key] = value


def can_send(codec: Mapping[str, object]) -> bool:
    """Whether the browser may send ``codec`` (an ORTC codec dict), per the SDK's rule.

    H265 is never sendable; VP9 profiles 1 and 3 and AV1 profile 1 are receive-only.
    """
    name = str(as_mapping(codec.get("rtpMap")).get("encodingName") or "").upper()
    parameters = as_mapping(as_mapping(codec.get("fmtp")).get("parameters"))
    match name:
        case "H265":
            return False
        case "VP9":
            return parameters.get("profile-id") not in {"1", "3"}
        case "AV1":
            return parameters.get("profile") != "1"
        case _:
            return True


def offer_to_ortc(offer_sdp: str, *, dtls_role: str | None = DEFAULT_ORTC_DTLS_ROLE) -> ClientOrtc:
    """The client ORTC for ``join_v3``: ICE and DTLS parameters plus send/recv/sendrecv capability buckets.

    Sendable codecs go to ``sendrecv`` and the rest to ``recv`` whatever the m-line direction; ``send`` stays
    empty and every extension goes to ``sendrecv`` (the shipped layout). Every codec carries an ``rrtr``
    feedback. ``cname`` is added when an offered ``a=ssrc`` declares one.

    Args:
        offer_sdp: The viewer's offer.
        dtls_role: ``dtlsParameters.role`` to declare (D4); ``None`` sends none, as the SDK does (Q2).

    Raises:
        SdpError: The offer has no media sections, no ICE credentials or no DTLS fingerprint.
    """
    parsed = _parse(offer_sdp)
    dtls: dict[str, object] = {"fingerprints": _fingerprints(parsed)}
    if dtls_role is not None:
        dtls["role"] = dtls_role
    caps: dict[str, dict[str, list[dict[str, object]]]] = {
        bucket: {"audioCodecs": [], "audioExtensions": [], "videoCodecs": [], "videoExtensions": []}
        for bucket in _BUCKETS
    }
    for media in parsed["media"]:
        if (kinds := KINDS.get(media.get("type", ""))) is None:
            continue
        codecs_key, extensions_key = kinds
        for codec in _codecs(media):
            caps["sendrecv" if can_send(codec) else "recv"][codecs_key].append(codec)
        caps["sendrecv"][extensions_key].extend(
            {"entry": ext["value"], "extensionName": ext["uri"]} for ext in media.get("ext", [])
        )
    ortc: ClientOrtc = {
        "iceParameters": _ice_parameters(parsed),
        "dtlsParameters": dtls,
        "rtpCapabilities": caps,
        "version": "2",
    }
    if (cname := _cname(parsed)) is not None:
        ortc["cname"] = cname
    return ortc


def negotiated_caps(gateway_ortc: Mapping[str, object]) -> dict[str, object]:
    """The gateway's RTP capabilities: the first non-empty object of ``sendrecv``, ``recv``, ``send``, else the flat block.

    The one home of the bucket rule; anything that is not an object reads as absent.
    """
    capabilities = as_mapping(gateway_ortc.get("rtpCapabilities"))
    for name in _CAPABILITY_ORDER:
        if bucket := as_mapping(capabilities.get(name)):
            return dict(bucket)
    return dict(capabilities)


def as_mapping(value: object) -> Mapping[str, object]:
    """``value`` when it is a JSON object, else an empty one; how the ORTC readers tolerate a malformed node."""
    return value if isinstance(value, Mapping) else {}


def _ice_parameters(parsed: Mapping[str, Any]) -> OrtcIceParameters:
    for section in (parsed, *parsed["media"]):
        if "iceUfrag" in section:
            if "icePwd" not in section:
                raise SdpError("offer has an ice-ufrag without an ice-pwd")
            return {"iceUfrag": section["iceUfrag"], "icePwd": section["icePwd"]}
    raise SdpError("offer has no ice-ufrag/ice-pwd")


def _fingerprints(parsed: Mapping[str, Any]) -> list[dict[str, str]]:
    for section in (parsed, *parsed["media"]):
        if fingerprint := section.get("fingerprint"):
            return [{"hashFunction": str(fingerprint["type"]), "fingerprint": str(fingerprint["hash"])}]
    raise SdpError("offer has no DTLS fingerprint")


def _codecs(media: Mapping[str, Any]) -> list[dict[str, object]]:
    codecs: list[dict[str, object]] = []
    for rtp in media.get("rtp", []):
        payload_type = rtp["payload"]
        rtp_map: dict[str, object] = {
            "encodingName": str(rtp["codec"]),
            "clockRate": rtp.get("rate") or DEFAULT_CLOCK_RATE,
        }
        if isinstance(encoding := rtp.get("encoding"), int):
            rtp_map["encodingParameters"] = encoding
        feedbacks = [
            {"type": fb["type"], "parameter": str(fb["subtype"])} if "subtype" in fb else {"type": fb["type"]}
            for fb in media.get("rtcpFb", [])
            if fb["payload"] in {payload_type, _WILDCARD_PAYLOAD}
        ]
        if {"type": "rrtr"} not in feedbacks:
            feedbacks.append({"type": "rrtr"})
        parameters: dict[str, str | None] = {}
        for fmtp in media.get("fmtp", []):
            if fmtp["payload"] == payload_type:
                parameters.update(_fmtp_parameters(str(fmtp["config"])))
        codecs.append(
            {
                "payloadType": payload_type,
                "rtpMap": rtp_map,
                "rtcpFeedbacks": feedbacks,
                "fmtp": {"parameters": parameters},
            }
        )
    return codecs


def _fmtp_parameters(config: str) -> dict[str, str | None]:
    """``a=fmtp`` parameters as a dict; a key without ``=`` maps to ``None``, as the SDK does."""
    parameters: dict[str, str | None] = {}
    for part in config.split(";"):
        key, has_value, value = part.partition("=")
        if key := key.strip():
            parameters[key] = value.strip() if has_value else None
    return parameters


def _cname(parsed: Mapping[str, Any]) -> str | None:
    return next(
        (
            str(ssrc["value"])
            for media in parsed["media"]
            for ssrc in media.get("ssrcs", [])
            if ssrc.get("attribute") == "cname"
        ),
        None,
    )
