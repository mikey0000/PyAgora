"""The RTM REST surface: ``POST /dev/v2/project/{app_id}/rtm/users/{user_id}/peer_messages`` (protocol.md §8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from tests.fakegateway._common import decode, host_index
from tests.fakegateway.state import RtmRecord

if TYPE_CHECKING:
    from tests.fakegateway._common import JsonObject
    from tests.fakegateway.state import FakeAgoraState

RTM_ROUTE = "/dev/v2/project/{app_id}/rtm/users/{user_id}/peer_messages"
RECORDED_HEADERS = ("Content-Type", "x-agora-token", "x-agora-uid", "Authorization")
_BODY_KEYS: dict[str, type] = {
    "destination": str,
    "enable_offline_messaging": bool,
    "enable_historical_messaging": bool,
}


class RtmRejectedError(Exception):
    """The request fails with ``status`` and a body naming ``reason``."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


def add_routes(app: web.Application, state: FakeAgoraState) -> None:
    async def peer_messages(request: web.Request) -> web.Response:
        app_id, user_id = request.match_info["app_id"], request.match_info["user_id"]
        wait_for_ack = request.query.get("wait_for_ack") == "true"
        headers = {name: request.headers[name] for name in RECORDED_HEADERS if name in request.headers}
        state.rtm_posts_seen += 1

        def record(status: int, body: JsonObject | None, payload: object = None, error: str | None = None) -> None:
            item = RtmRecord(host_index(request), status, app_id, user_id, wait_for_ack, headers, body, payload, error)
            state.log.record(state.log.rtm_messages, item)

        if state.rtm_posts_seen <= state.rtm_fail_first:
            record(503, None, error="rtm_fail_first")
            return web.Response(status=503, text="Service Unavailable")
        body = decode(await request.read())
        try:
            _validate(state, app_id, user_id, request, body)
        except RtmRejectedError as exc:
            record(exc.status, body, error=str(exc))
            return web.json_response({"result": "failed", "code": str(exc)}, status=exc.status)
        assert body is not None
        record(200, body, decode(body["payload"]))
        code = state.rtm_code or ("message_delivered" if wait_for_ack else "message_sent")
        return web.json_response({"result": "success", "code": code, "request_id": f"rtm-{state.rtm_posts_seen}"})

    app.router.add_post(RTM_ROUTE, peer_messages)


def _validate(state: FakeAgoraState, app_id: str, user_id: str, request: web.Request, body: JsonObject | None) -> None:
    if app_id != state.app_id:
        raise RtmRejectedError(404, "unknown project")
    if request.content_type != "application/json":
        raise RtmRejectedError(400, "content type is not application/json")
    token = state.rtm_token
    if request.headers.get("x-agora-token") != token:
        raise RtmRejectedError(401, "x-agora-token rejected")
    if request.headers.get("x-agora-uid") != user_id:
        raise RtmRejectedError(401, "x-agora-uid does not match the path user")
    if request.headers.get("Authorization") != f"agora token={token}":
        raise RtmRejectedError(401, "Authorization rejected")
    if user_id != state.rtm_user_id:
        raise RtmRejectedError(401, "unknown user")
    if body is None:
        raise RtmRejectedError(400, "body is not a JSON object")
    for key, kind in _BODY_KEYS.items():
        if not isinstance(body.get(key), kind):
            raise RtmRejectedError(400, f"body.{key} is missing or not a {kind.__name__}")
    if not isinstance(body.get("payload"), str) or decode(body["payload"]) is None:
        raise RtmRejectedError(400, "body.payload is not a JSON object encoded as a string")
