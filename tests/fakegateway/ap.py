"""The access-point surface: ``POST /api/v2/transpond/webrtc`` for choose_server and update_ticket (protocol.md §1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from tests.fakegateway._common import (
    FLAG_GATEWAY,
    SERVICE_BLOCKS,
    decode,
    dtls_fingerprint,
    host_index,
    now_ms,
)
from tests.fakegateway.state import ApRequestRecord

if TYPE_CHECKING:
    from tests.fakegateway._common import JsonObject
    from tests.fakegateway.state import Edge, FakeAgoraState

AP_ROUTE = "/api/v2/transpond/webrtc"
URI_CHOOSE_SERVER = 22
URI_UPDATE_TICKET = 28
_ENVELOPE_KEYS: dict[str, type | tuple[type, ...]] = {
    "appid": str,
    "client_ts": int,
    "opid": int,
    "sid": str,
    "request_bodies": list,
}
_BUFFER_KEYS: dict[str, type | tuple[type, ...]] = {
    "cname": str,
    "detail": dict,
    "key": str,
    "service_ids": list,
    "uid": int,
}


class EnvelopeError(ValueError):
    """The AP request does not have the shape protocol.md §1.1 records."""


def add_routes(app: web.Application, state: FakeAgoraState) -> None:
    async def transpond(request: web.Request) -> web.Response:
        index = host_index(request)
        if index < state.ap_fail_hosts:
            state.log.record(state.log.ap_requests, ApRequestRecord(index, 503, None, "ap_fail_hosts"))
            return web.Response(status=503, text="Service Unavailable")
        try:
            envelope = await _read_envelope(request)
        except EnvelopeError as exc:
            state.log.record(state.log.ap_requests, ApRequestRecord(index, 400, None, str(exc)))
            return web.json_response({"error": str(exc)}, status=400)
        state.log.record(state.log.ap_requests, ApRequestRecord(index, 200, envelope))
        return web.json_response(build_response(state, envelope))

    app.router.add_post(AP_ROUTE, transpond)


async def _read_envelope(request: web.Request) -> JsonObject:
    if not request.content_type.startswith("multipart/form-data"):
        raise EnvelopeError(f"content type is {request.content_type!r}, not multipart/form-data")
    form = await request.post()
    field = form.get("request")
    if field is None:
        raise EnvelopeError("form has no 'request' field")
    match field:
        case str():
            text = field
        case bytes() | bytearray():
            text = bytes(field).decode()
        case _:
            text = field.file.read().decode()
    if (envelope := decode(text)) is None:
        raise EnvelopeError("'request' is not a JSON object")
    _require(envelope, _ENVELOPE_KEYS, "envelope")
    if not envelope["request_bodies"]:
        raise EnvelopeError("request_bodies is empty")
    for i, body in enumerate(envelope["request_bodies"]):
        where = f"request_bodies[{i}]"
        if not isinstance(body, dict):
            raise EnvelopeError(f"{where} is not an object")
        _require(body, {"uri": int, "buffer": dict}, where)
        if body["uri"] not in {URI_CHOOSE_SERVER, URI_UPDATE_TICKET}:
            raise EnvelopeError(f"{where}.uri {body['uri']} is not 22 or 28")
        _require(body["buffer"], _BUFFER_KEYS, f"{where}.buffer")
        if unknown := [s for s in body["buffer"]["service_ids"] if s not in SERVICE_BLOCKS]:
            raise EnvelopeError(f"{where}.buffer.service_ids has unknown ids {unknown}")
        if body["uri"] == URI_UPDATE_TICKET:
            _require(body["buffer"], {"edges_services": list}, f"{where}.buffer")
    return envelope


def _require(obj: JsonObject, keys: dict[str, type | tuple[type, ...]], where: str) -> None:
    for key, kind in keys.items():
        if key not in obj:
            raise EnvelopeError(f"{where} is missing {key!r}")
        if not isinstance(obj[key], kind) or isinstance(obj[key], bool):
            raise EnvelopeError(f"{where}.{key} has the wrong type")


def build_response(state: FakeAgoraState, envelope: JsonObject) -> JsonObject:
    """One response block per requested service id, per request body, ordered by ``envelope_flag_order``."""
    blocks = [
        {"uri": body["uri"] + 1, "buffer": _block(state, name, body["buffer"])}
        for body in envelope["request_bodies"]
        for name in state.envelope_flag_order
        if name in {SERVICE_BLOCKS[s][0] for s in body["buffer"]["service_ids"]}
    ]
    return {"enter_ts": now_ms(state.clock), "opid": envelope["opid"], "detail": {}, "response_body": blocks}


def _block(state: FakeAgoraState, name: str, buffer: JsonObject) -> JsonObject:
    common = {
        "uid": buffer["uid"] or state.viewer_uid,
        "cid": state.cid,
        "cname": buffer["cname"],
        "cert": state.ticket,
    }
    if name == "gateway":
        edges = state.gateway_edges
        return {
            "code": 0,
            "flag": FLAG_GATEWAY,
            **common,
            "detail": _gateway_detail(state, edges),
            "edges_services": _edges(edges),
        }
    ok = state.ap_turn_code == 0
    return {
        "code": state.ap_turn_code,
        "flag": SERVICE_BLOCKS[26][1],
        **common,
        "detail": {"8": state.turn_username, "4": state.turn_password} if ok else {},
        "edges_services": _edges(state.turn_edges) if ok else [],
    }


def _gateway_detail(state: FakeAgoraState, edges: tuple[Edge, ...]) -> JsonObject:
    first = edges[0]
    return {
        "1": first.ip,
        "8": str(state.vid),
        "19": ";".join(f"sha-256 {dtls_fingerprint(f'edge-{e.ip}:{e.port}')}" for e in edges),
        "23": "GLOBAL",
        "502": first.ip,
        "candidate": f"{first.ip}:{first.port}",
    }


def _edges(edges: tuple[Edge, ...]) -> list[JsonObject]:
    return [{"ip": e.ip, "port": e.port} for e in edges]
