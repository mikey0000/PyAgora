"""``APResponse``: an access-point answer parsed into edges, tickets, ICE servers and the join's ``ap_response``.

Merge rules are ``docs/analysis/divergence.md`` §1.1; the wire shape is ``docs/protocol.md`` §1.3-§1.4.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING

from pyagorartc.ap.password import derive_password
from pyagorartc.const import (
    AP_FLAG_GATEWAY,
    AP_FLAG_TURN,
    EDGE_DOMAIN_SUFFIX,
    GATEWAY_TURN_PORT_OFFSET,
    TURN_PORT,
    TURNS_PORT,
)
from pyagorartc.exceptions import APError, APRejectedError
from pyagorartc.models import EdgeAddress, ICEServer, TurnCredentialStrategy, TurnMode, as_int, fingerprint

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

_DETAIL_FINGERPRINTS = "19"
_DEFAULT_FINGERPRINT_ALGORITHM = "sha-256"
_DETAIL_TURN_USERNAME = "8"
_DETAIL_TURN_CREDENTIAL = "4"
_MISSING_CODE = -1

type JsonObject = Mapping[str, object]


def _as_object(value: object) -> JsonObject:
    return value if isinstance(value, Mapping) else {}


def fingerprints_from_edge(edge: EdgeAddress) -> list[dict[str, str]]:
    """``edge``'s detail-19 fingerprint as gateway-ORTC ``dtlsParameters.fingerprints`` (D26); empty when it has none.

    Accepts ``sha-256 AA:BB:…`` and a bare ``AA:BB:…`` (read as sha-256), as both shipped hosts parsed it.
    """
    match (edge.fingerprint or "").split():
        case [value]:
            return [{"algorithm": _DEFAULT_FINGERPRINT_ALGORITHM, "fingerprint": value}]
        case [algorithm, value]:
            return [{"algorithm": algorithm, "fingerprint": value}]
        case []:
            return []
        case parts:
            _LOGGER.debug("Ignoring an AP edge fingerprint of %s words; expected 'algorithm value'", len(parts))
            return []


def _parse_edges(raw: object, ticket: str, detail: JsonObject) -> tuple[EdgeAddress, ...]:
    # Fingerprints are matched to edges by index in the list as sent, before filtering (SDK:48698-48704).
    fingerprints = [fp.strip() or None for fp in str(detail.get(_DETAIL_FINGERPRINTS) or "").split(";")]
    edges: list[EdgeAddress] = []
    for index, entry in enumerate(raw if isinstance(raw, list) else []):
        edge = _as_object(entry)
        ip = edge.get("ip")
        port = as_int(edge.get("port"), 0)
        if not isinstance(ip, str) or not ip or port <= 0:
            continue
        edges.append(
            EdgeAddress(
                ip=ip,
                port=port,
                ticket=ticket,
                fingerprint=fingerprints[index] if index < len(fingerprints) else None,
            )
        )
    return tuple(edges)


@dataclass(frozen=True, repr=False)
class APBlock:
    """One successful ``response_body[].buffer``, keyed by its ``flag``.

    ``detail`` is the top-level ``detail`` with the buffer's own merged over it (SDK:43726). Edges
    carry the block's ticket and fingerprint but no TURN credentials; ``get_ice_servers`` resolves
    those per ``TurnCredentialStrategy``.
    """

    flag: int
    code: int
    uid: int
    cid: int
    cname: str
    ticket: str
    detail: dict[str, object]
    addresses: tuple[EdgeAddress, ...]

    @classmethod
    def from_buffer(cls, buffer: JsonObject, base_detail: JsonObject) -> APBlock:
        """Parse one buffer; edges missing an ``ip`` or ``port`` are dropped."""
        detail = {**base_detail, **_as_object(buffer.get("detail"))}
        ticket = str(buffer.get("cert") or "")
        return cls(
            flag=as_int(buffer.get("flag"), 0),
            code=as_int(buffer.get("code"), _MISSING_CODE),
            uid=as_int(buffer.get("uid"), 0),
            cid=as_int(buffer.get("cid"), 0),
            cname=str(buffer.get("cname") or ""),
            ticket=ticket,
            detail=detail,
            addresses=_parse_edges(buffer.get("edges_services"), ticket, detail),
        )

    def __repr__(self) -> str:
        return (
            f"APBlock(flag={self.flag}, code={self.code}, uid={self.uid}, cid={self.cid}, cname={self.cname!r}, "
            f"ticket=<{fingerprint(self.ticket)}>, detail_keys={sorted(self.detail)}, addresses={len(self.addresses)})"
        )


def _turn_urls(ip: str, mode: TurnMode) -> list[str]:
    urls: list[str] = []
    if mode in {TurnMode.ALL, TurnMode.UDP_ONLY}:
        urls.append(f"turn:{ip}:{TURN_PORT}?transport=udp")
    if mode in {TurnMode.ALL, TurnMode.TCP_ONLY}:
        urls.append(f"turn:{ip}:{TURN_PORT}?transport=tcp")
    if mode in {TurnMode.ALL, TurnMode.TLS_ONLY}:
        urls.append(f"turns:{ip.replace('.', '-')}{EDGE_DOMAIN_SUFFIX}:{TURNS_PORT}?transport=tcp")
    return urls


def _turn_credentials(block: APBlock, strategy: TurnCredentialStrategy, uid: int) -> tuple[str, str]:
    username, credential = str(uid), derive_password(uid)
    # Only the TURN block's detail 8/4 are credentials; the gateway block's detail 8 is its vid.
    if strategy is TurnCredentialStrategy.DETAIL_FIRST and block.flag == AP_FLAG_TURN:
        # Each key falls back on its own, as the PetKit copy shipped (D12).
        username = str(block.detail.get(_DETAIL_TURN_USERNAME) or username)
        credential = str(block.detail.get(_DETAIL_TURN_CREDENTIAL) or credential)
    return username, credential


@dataclass(frozen=True, repr=False)
class APResponse:
    """A parsed ``choose_server`` / ``update_ticket`` answer.

    ``responses`` holds every successful block by flag, however many there are. The primary block
    is flag 4096 when it succeeded, else the first successful block, and every primary scalar
    (``ticket``, ``uid``, ``cid``, ``cname``, ``detail``, ``addresses``) is read from that one block.
    """

    responses: dict[int, APBlock]
    primary_flag: int
    server_ts: int
    opid: int

    @classmethod
    def from_api_response(cls, data: JsonObject, *, clock: Callable[[], float] = time.time) -> APResponse:
        """Parse the AP's JSON body; ``clock`` (seconds) stands in for a missing ``enter_ts``.

        Raises:
            APRejectedError: Every buffer carried a non-zero ``code``; ``codes`` maps flag to code.
            APError: The body has no ``response_body`` entries at all.

        """
        body = data.get("response_body")
        if not isinstance(body, list) or not body:
            raise APError("access point response has no response_body")
        base_detail = _as_object(data.get("detail"))
        blocks: dict[int, APBlock] = {}
        failed: dict[int, int] = {}
        for item in body:
            block = APBlock.from_buffer(_as_object(_as_object(item).get("buffer")), base_detail)
            if block.code != 0:
                _LOGGER.debug("AP block flag=%s failed with code=%s; skipped", block.flag, block.code)
                failed[block.flag] = block.code
                continue
            blocks[block.flag] = block
        if not blocks:
            raise APRejectedError(failed)
        enter_ts = data.get("enter_ts")
        return cls(
            responses=blocks,
            primary_flag=AP_FLAG_GATEWAY if AP_FLAG_GATEWAY in blocks else next(iter(blocks)),
            server_ts=int(clock() * 1000) if enter_ts is None else as_int(enter_ts, 0),
            opid=as_int(data.get("opid"), 0),
        )

    @property
    def primary(self) -> APBlock:
        """The block every primary scalar comes from."""
        return self.responses[self.primary_flag]

    @property
    def code(self) -> int:
        """The primary block's ``code`` (always 0: failed blocks are not kept)."""
        return self.primary.code

    @property
    def ticket(self) -> str:
        """The primary block's ``cert``."""
        return self.primary.ticket

    @property
    def uid(self) -> int:
        """The uid the AP assigned in the primary block."""
        return self.primary.uid

    @property
    def cid(self) -> int:
        """The primary block's channel id."""
        return self.primary.cid

    @property
    def cname(self) -> str:
        """The primary block's channel name."""
        return self.primary.cname

    @property
    def detail(self) -> dict[str, object]:
        """The primary block's merged ``detail``."""
        return self.primary.detail

    @property
    def addresses(self) -> list[EdgeAddress]:
        """The primary block's edges."""
        return list(self.primary.addresses)

    def get_gateway_addresses(self) -> list[EdgeAddress]:
        """The WebSocket gateway edges (flag 4096) in AP order; empty when that block failed."""
        return list(block.addresses) if (block := self.responses.get(AP_FLAG_GATEWAY)) else []

    def get_turn_addresses(self) -> list[EdgeAddress]:
        """The TURN edges (flag 4194310) in AP order; empty when that block failed or was not asked for."""
        return list(block.addresses) if (block := self.responses.get(AP_FLAG_TURN)) else []

    def get_ice_servers(
        self,
        *,
        use_all_turn_servers: bool = False,
        turn_mode: TurnMode = TurnMode.ALL,
        strategy: TurnCredentialStrategy = TurnCredentialStrategy.UID,
        uid: int | None = None,
    ) -> list[ICEServer]:
        """ICE servers for a viewer: one entry per transport per TURN edge, one url each.

        UDP and TCP use ``turn:{ip}:3478``; TLS uses ``turns:{a-b-c-d}.edge.agora.io:443``. Without
        TURN edges the primary edges are used, as both hosts shipped. Credentials follow ``strategy``
        (D12), with AP detail 8/4 read only from the TURN block; ``uid`` replaces the block's uid for the
        uid-derived pair.
        """
        turn = self.responses.get(AP_FLAG_TURN)
        block = turn if turn is not None and turn.addresses else self.primary
        if block is not turn:
            _LOGGER.debug("no TURN edges in the AP response; using flag %s edges", block.flag)
        addresses = block.addresses if use_all_turn_servers else block.addresses[:1]
        username, credential = _turn_credentials(block, strategy, block.uid if uid is None else uid)
        return [
            ICEServer(urls=[url], username=username, credential=credential)
            for address in addresses
            for url in _turn_urls(address.ip, turn_mode)
        ]

    def turn_server_config(self, gateway: EdgeAddress | None, token: str | None) -> dict[str, object]:
        """The SDK's ``turnServer`` object (protocol.md §1.4); pure, and unused by the session.

        ``servers`` are the TURN edges with uid-derived credentials. ``serversFromGateway`` holds the
        connected gateway on its port + 30 with the channel token as password, only when both are given.
        """
        servers = [
            {
                "turnServerURL": address.ip,
                "tcpport": address.port,
                "udpport": address.port,
                "username": str(self.uid),
                "password": derive_password(self.uid),
                "forceturn": False,
                "security": True,
            }
            for address in self.get_turn_addresses()
        ]
        from_gateway: list[dict[str, object]] = []
        if gateway is not None and token:
            port = gateway.port + GATEWAY_TURN_PORT_OFFSET
            from_gateway.append(
                {
                    "username": str(self.uid),
                    "password": token,
                    "turnServerURL": gateway.ip,
                    "tcpport": port,
                    "udpport": port,
                    "forceturn": False,
                }
            )
        return {"mode": "manual", "servers": servers, "serversFromGateway": from_gateway}

    def to_ap_response(self, flag: int | None = None) -> dict[str, object]:
        """One block (the primary by default) as the join's ``ap_response``: ``cert`` doubled as ``ticket``.

        Raises:
            APError: ``flag`` names a block this response does not hold.

        """
        if flag is None:
            block = self.primary
        elif (found := self.responses.get(flag)) is not None:
            block = found
        else:
            raise APError(f"access point response has no block for flag {flag}")
        return {
            "code": block.code,
            "server_ts": self.server_ts,
            "uid": block.uid,
            "cid": block.cid,
            "cname": block.cname,
            "detail": dict(block.detail),
            "flag": block.flag,
            "opid": self.opid,
            "cert": block.ticket,
            "ticket": block.ticket,
        }

    def __repr__(self) -> str:
        return (
            f"APResponse(primary_flag={self.primary_flag}, flags={list(self.responses)}, uid={self.uid}, "
            f"cid={self.cid}, cname={self.cname!r}, ticket=<{fingerprint(self.ticket)}>, "
            f"server_ts={self.server_ts}, opid={self.opid})"
        )
