"""Build the answer SDP a viewer applies from the gateway's ``join_v3`` ORTC (``docs/protocol.md`` §6.2).

Lifted from HA-Luba's shipped answer builder, with PetKit's offer-payload fallback, audio-disable and declared
remote SSRC. ``tests/fixtures/sdp/chrome_answer_shipped.sdp`` pins parity with the shipped output.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any, cast

from pyagorartc.exceptions import SdpError
from pyagorartc.models import as_int
from pyagorartc.sdp.offer import DEFAULT_CLOCK_RATE, KINDS, as_mapping, negotiated_caps, parse_offer

if TYPE_CHECKING:
    from pyagorartc.models import RemoteStream, SessionOptions

_LOGGER = logging.getLogger(__name__)

MID_EXTENSION_URI = "urn:ietf:params:rtp-hdrext:sdes:mid"
#: D16: the edge numbers video mid 2 internally, so a negotiated MID makes Chrome drop every video packet.
STRIPPED_EXTENSIONS: frozenset[str] = frozenset({MID_EXTENSION_URI})

# D5; RFC 5763 §5 forbids actpass in an answer, so a gateway that will take either role gets active.
_SETUP_FOR_ROLE = {"server": "passive", "client": "active", "auto": "active"}
# The live gateway reports "client"; an absent role is read the same way (the shipped default).
_DEFAULT_GATEWAY_ROLE = "client"
_ANSWER_DIRECTION = {"recvonly": "sendonly", "sendonly": "recvonly", "sendrecv": "sendrecv", "inactive": "inactive"}
_DEFAULT_OFFER_DIRECTION = "sendrecv"
# RFC 3551 §6: 0-34 are statically assigned; anything above means nothing without an a=rtpmap (D25).
_FIRST_UNASSIGNED_PAYLOAD_TYPE = 35
_DEFAULT_PRIORITY = 2103266323
_STREAM_ID = "agora"
_TRACK_ID = "agora-video"
_SESSION_HEADER = ("v=0", "o=- 0 0 IN IP4 127.0.0.1", "s=AgoraGateway", "t=0 0")
_REQUIRED_SESSION_LINES = ("v=", "o=", "s=", "t=")
_MIN_MEDIA_FIELDS = 4


def setup_for_role(role: str | None) -> str:
    """The answer's ``a=setup`` for the gateway's DTLS role (D5); ``None`` reads as ``client``, ``auto`` as ``active``.

    Raises:
        SdpError: The role is not ``server``, ``client`` or ``auto``.
    """
    if (setup := _SETUP_FOR_ROLE.get(role or _DEFAULT_GATEWAY_ROLE)) is None:
        raise SdpError(f"gateway sent an unknown DTLS role {role!r}")
    return setup


def offers_rtx(gateway_ortc: Mapping[str, object]) -> bool:
    """Whether the gateway lists an ``rtx`` video codec; the live gateway lists none (``docs/protocol.md`` §2.5)."""
    codecs = negotiated_caps(gateway_ortc).get("videoCodecs")
    return any(
        str(as_mapping(as_mapping(codec).get("rtpMap")).get("encodingName") or "").lower() == "rtx"
        for codec in (codecs if isinstance(codecs, list) else [])
    )


def answer_from_ortc(
    gateway_ortc: Mapping[str, object],
    offer_sdp: str,
    *,
    options: SessionOptions,
    remote_video: RemoteStream | None = None,
) -> str:
    """The answer SDP for ``offer_sdp``: one section per offer m-line, in offer order, from the gateway ORTC.

    The answer is ``a=ice-lite``, carries the gateway's ICE credentials, first fingerprint and every candidate in
    each section, and copies the gateway's codecs unchanged. When the gateway lists no codec for a section the
    offer's payload types that carry an ``a=rtpmap`` (or are statically assigned) are answered instead, and a
    section left with none is rejected. Extensions are those both sides list, under the offer's ids, minus
    ``STRIPPED_EXTENSIONS`` when ``options.strip_mid_extension``. ``remote_video`` declares that stream's SSRCs
    and ``a=msid`` in the first video section (D17); its RTX group only when the gateway offers RTX. An offer
    m-line without ``a=mid`` gets none in the answer and is left out of BUNDLE.

    Raises:
        SdpError: The offer cannot be parsed, or the gateway ORTC lacks ICE credentials, a fingerprint, a known
            DTLS role or a codec's ``payloadType``/``encodingName``, or a required node is not an object
            (D9: nothing is made up).
    """
    offer = cast("Mapping[str, Any]", parse_offer(offer_sdp))
    ice = as_mapping(gateway_ortc.get("iceParameters"))
    dtls = as_mapping(gateway_ortc.get("dtlsParameters"))
    role = dtls.get("role")
    transport = [
        f"a=ice-ufrag:{_required(ice, 'iceUfrag')}",
        f"a=ice-pwd:{_required(ice, 'icePwd')}",
        "a=ice-options:trickle",
        f"a=fingerprint:{_fingerprint(dtls)}",
        f"a=setup:{setup_for_role(role if isinstance(role, str) else None)}",
    ]
    candidates = _candidate_lines(ice.get("candidates"))
    caps = negotiated_caps(gateway_ortc)
    stripped = STRIPPED_EXTENSIONS if options.strip_mid_extension else frozenset()
    ssrc_lines = _ssrc_lines(remote_video, gateway_ortc) if remote_video is not None else []

    sections: list[list[str]] = []
    rejected: set[str] = set()
    for media in offer["media"]:
        kind = str(media.get("type", ""))
        mid = str(mid_value) if (mid_value := media.get("mid")) is not None else None
        mid_lines = [f"a=mid:{mid}"] if mid is not None else []
        if kind not in KINDS:
            sections.append(_rejected_section(media, mid, rejected))
            continue
        codecs_key, extensions_key = KINDS[kind]
        # sdp_transform only yields the four RFC 3264 directions.
        direction = _ANSWER_DIRECTION[media.get("direction", _DEFAULT_OFFER_DIRECTION)]
        if kind == "audio" and options.disable_audio:
            direction = "inactive"
        if codecs := _codec_list(caps, codecs_key):
            payload_types, codec_lines = _gateway_codecs(codecs, codecs_key)
        else:
            payload_types, codec_lines = _offer_codecs(media)
        if not payload_types:
            _LOGGER.debug("Rejecting offer %s section %s: no payload type it can answer", kind, mid)
            sections.append(_rejected_section(media, mid, rejected))
            continue
        lines = [
            f"m={kind} 9 UDP/TLS/RTP/SAVPF {payload_types}",
            "c=IN IP4 127.0.0.1",
            "a=rtcp:9 IN IP4 0.0.0.0",
            *transport,
            *mid_lines,
            *candidates,
            *_extension_lines(media, caps.get(extensions_key), stripped),
            f"a={direction}",
            "a=rtcp-mux",
            "a=rtcp-rsize",
            *codec_lines,
        ]
        if kind == "video":
            lines.extend(ssrc_lines)
            ssrc_lines = []
        sections.append(lines)

    header = list(_SESSION_HEADER)
    if bundle := _bundle_mids(offer, rejected):
        header.append(f"a=group:BUNDLE {bundle}")
    header.append("a=ice-lite")
    if "extmapAllowMixed" in offer:
        header.append("a=extmap-allow-mixed")
    header.append("a=msid-semantic: WMS")
    answer = "".join(f"{line}\r\n" for line in [*header, *(line for section in sections for line in section)])
    validate_answer(answer)
    return answer


def validate_answer(sdp: str, *, min_media_sections: int = 1) -> None:
    """Check ``sdp`` has the session lines, at least ``min_media_sections`` m-lines, and no m-line without formats.

    PetKit's audio-disabled answers are why the floor defaults to one section, not HA-Luba's two.

    Raises:
        SdpError: Naming the first check that failed.
    """
    lines = [line.strip() for line in sdp.splitlines() if line.strip()]
    if not lines:
        raise SdpError("answer SDP is empty")
    for prefix in _REQUIRED_SESSION_LINES:
        if not any(line.startswith(prefix) for line in lines):
            raise SdpError(f"answer SDP has no {prefix} line")
    media_lines = [line for line in lines if line.startswith("m=")]
    if len(media_lines) < min_media_sections:
        raise SdpError(f"answer SDP has {len(media_lines)} media sections, expected at least {min_media_sections}")
    for line in media_lines:
        if len(line.split()) < _MIN_MEDIA_FIELDS:
            raise SdpError(f"answer SDP media line has no payload types: {line!r}")


def _required(ice: Mapping[str, object], key: str) -> str:
    if not (value := ice.get(key)) or isinstance(value, Mapping | list):
        raise SdpError(f"gateway ORTC has no iceParameters.{key}")
    return str(value)


def _fingerprint(dtls: Mapping[str, object]) -> str:
    fingerprints = dtls.get("fingerprints")
    for entry in fingerprints if isinstance(fingerprints, list) else []:
        if not isinstance(entry, Mapping):
            _LOGGER.debug("Skipping a gateway DTLS fingerprint that is not an object")
            continue
        if value := entry.get("fingerprint"):
            # The gateway writes "algorithm"; fingerprints merged in from the AP response carry "hashFunction".
            return f"{entry.get('hashFunction') or entry.get('algorithm') or 'sha-256'} {value}"
        break
    raise SdpError("gateway ORTC has no DTLS fingerprint")


def _candidate_lines(candidates: object) -> list[str]:
    lines = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, Mapping):
            _LOGGER.debug("Skipping a gateway candidate that is not an object")
            continue
        if candidate.get("ip") is None or candidate.get("port") is None:
            _LOGGER.debug("Skipping gateway candidate without an address: %s", sorted(candidate))
            continue
        line = (
            f"a=candidate:{candidate.get('foundation', 'udpcandidate')} 1 {candidate.get('protocol', 'udp')} "
            f"{candidate.get('priority', _DEFAULT_PRIORITY)} {candidate['ip']} {candidate['port']} "
            f"typ {candidate.get('type', 'host')}"
        )
        if (generation := candidate.get("generation")) is not None:
            line += f" generation {generation}"
        lines.append(line)
    return lines


def _codec_list(caps: Mapping[str, object], key: str) -> list[object]:
    if (codecs := caps.get(key)) is None:
        return []
    if not isinstance(codecs, list):
        raise SdpError(f"gateway ORTC rtpCapabilities.{key} is not a list")
    return codecs


def _gateway_codecs(codecs: list[object], key: str) -> tuple[str, list[str]]:
    payload_types = []
    lines = []
    for codec in codecs:
        if not isinstance(codec, Mapping):
            raise SdpError(f"gateway ORTC {key} entry is not an object")
        if (payload_type := codec.get("payloadType")) is None:
            raise SdpError("gateway ORTC codec has no payloadType")
        rtp_map = as_mapping(codec.get("rtpMap"))
        if not (name := rtp_map.get("encodingName")):
            raise SdpError(f"gateway ORTC codec {payload_type} has no rtpMap.encodingName")
        rtpmap = f"a=rtpmap:{payload_type} {name}/{rtp_map.get('clockRate', DEFAULT_CLOCK_RATE)}"
        if encoding := rtp_map.get("encodingParameters"):
            rtpmap += f"/{encoding}"
        payload_types.append(str(payload_type))
        lines.append(rtpmap)
        lines.extend(_feedback_lines(payload_type, codec.get("rtcpFeedbacks")))
        if parameters := as_mapping(as_mapping(codec.get("fmtp")).get("parameters")):
            config = ";".join(key if value is None else f"{key}={value}" for key, value in parameters.items())
            lines.append(f"a=fmtp:{payload_type} {config}")
    return " ".join(payload_types), lines


def _feedback_lines(payload_type: object, feedbacks: object) -> list[str]:
    lines = []
    for feedback in feedbacks if isinstance(feedbacks, list) else []:
        if not isinstance(feedback, Mapping) or not (kind := feedback.get("type")):
            _LOGGER.debug("Skipping a gateway rtcpFeedback without a type for codec %s", payload_type)
            continue
        parameter = feedback.get("parameter")
        lines.append(f"a=rtcp-fb:{payload_type} {kind}" + (f" {parameter}" if parameter else ""))
    return lines


def _offer_codecs(media: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The offer's formats that mean something on their own: an ``a=rtpmap`` or a static assignment (D25)."""
    rtpmaps = {str(rtp["payload"]): rtp for rtp in media.get("rtp", [])}
    kept = [
        payload
        for payload in str(media["payloads"]).split()
        if payload in rtpmaps
        or ((number := as_int(payload)) is not None and 0 <= number < _FIRST_UNASSIGNED_PAYLOAD_TYPE)
    ]
    lines = []
    for payload in kept:
        if (rtp := rtpmaps.get(payload)) is None:
            continue
        rtpmap = f"a=rtpmap:{payload} {rtp['codec']}/{rtp.get('rate') or DEFAULT_CLOCK_RATE}"
        if (encoding := rtp.get("encoding")) is not None:
            rtpmap += f"/{encoding}"
        lines.append(rtpmap)
    lines.extend(
        f"a=fmtp:{fmtp['payload']} {fmtp['config']}" for fmtp in media.get("fmtp", []) if str(fmtp["payload"]) in kept
    )
    return " ".join(kept), lines


def _extension_lines(media: Mapping[str, Any], gateway_extensions: object, stripped: frozenset[str]) -> list[str]:
    offered = {str(ext["uri"]): ext["value"] for ext in media.get("ext", [])}
    lines = []
    for ext in gateway_extensions if isinstance(gateway_extensions, list) else []:
        if not isinstance(ext, Mapping):
            _LOGGER.debug("Skipping a gateway header extension that is not an object")
            continue
        if (uri := str(ext.get("extensionName"))) in offered and uri not in stripped:
            lines.append(f"a=extmap:{offered[uri]} {uri}")
    return lines


def _ssrc_lines(stream: RemoteStream, gateway_ortc: Mapping[str, object]) -> list[str]:
    gateway_cname = gateway_ortc.get("cname")
    cname = stream.cname or (gateway_cname if isinstance(gateway_cname, str) and gateway_cname else _STREAM_ID)
    msid = f"{_STREAM_ID} {_TRACK_ID}"
    lines = [
        f"a=msid:{msid}",
        f"a=ssrc:{stream.ssrc} cname:{cname}",
        f"a=ssrc:{stream.ssrc} msid:{msid}",
        f"a=ssrc:{stream.ssrc} mslabel:{_STREAM_ID}",
        f"a=ssrc:{stream.ssrc} label:{_TRACK_ID}",
    ]
    if stream.rtx_ssrc is not None and offers_rtx(gateway_ortc):
        lines += [f"a=ssrc-group:FID {stream.ssrc} {stream.rtx_ssrc}", f"a=ssrc:{stream.rtx_ssrc} cname:{cname}"]
    return lines


def _rejected_section(media: Mapping[str, Any], mid: str | None, rejected: set[str]) -> list[str]:
    lines = [f"m={media['type']} 0 {media['protocol']} {media['payloads']}", "c=IN IP4 0.0.0.0"]
    if mid is not None:
        rejected.add(mid)
        lines.append(f"a=mid:{mid}")
    return lines


def _bundle_mids(offer: Mapping[str, Any], rejected: set[str]) -> str:
    bundle = next((group for group in offer.get("groups", []) if group["type"] == "BUNDLE"), None)
    if bundle is not None:
        mids = bundle["mids"].split()
    else:
        mids = [str(media["mid"]) for media in offer["media"] if media.get("mid") is not None]
    return " ".join(mid for mid in mids if mid not in rejected)
