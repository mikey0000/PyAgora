"""``AgoraAPClient``: the access-point HTTP call (``choose_server``, ``update_ticket``) with host fallback.

Every HTTP exchange goes through ``AgoraAPClient._post``, the same seam ``RtmRestClient`` has, so a unit
test replaces that one method on the instance with a recorder instead of mocking aiohttp.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, Self
from urllib.parse import urlsplit

import aiohttp

from pyagora.ap.response import APResponse
from pyagora.const import (
    AP_HOSTS,
    AP_PATH,
    AP_QUERY,
    AP_TIMEOUT_S,
    AP_URI_CHOOSE_SERVER,
    AP_URI_UPDATE_TICKET,
    DEFAULT_SERVICE_IDS,
    ROLE_HOST,
    SERVICE_GATEWAY,
)
from pyagora.exceptions import APError, APRejectedError
from pyagora.models import fingerprint

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import TracebackType

    from pyagora.models import ChannelCredentials, EdgeAddress

_LOGGER = logging.getLogger(__name__)

_REQUEST_FIELD = "request"
_HTTP_OK = 200
_SID_BOUND = 2**31  # shipped: sid is a random int below 2**31, sent as a string
_OPID_BOUND = 10**12


def build_request_payload(  # noqa: PLR0913 -- pure: each wire value arrives as an argument
    creds: ChannelCredentials,
    *,
    uri: int,
    service_ids: Sequence[int],
    sid: str,
    opid: int,
    client_ts: int,
    role: int = ROLE_HOST,
    edges: Sequence[EdgeAddress] = (),
) -> dict[str, object]:
    """The AP request (protocol.md §1.1); pure, so ids and ``client_ts`` (ms) come from the caller.

    ``detail`` carries ``11``/``22`` (area code) and ``17`` (role), plus ``6`` when the credentials have a
    string uid (Q9). ``edges`` become ``edges_services`` for ``update_ticket`` and are omitted when empty.
    """
    detail: dict[str, str] = {"11": creds.area_code}
    if role:
        detail["17"] = str(role)
    detail["22"] = creds.area_code
    if creds.string_uid:
        detail["6"] = creds.string_uid
    buffer: dict[str, object] = {
        "cname": creds.channel_name,
        "detail": detail,
        "key": creds.token,
        "service_ids": list(service_ids),
        "uid": creds.uid,
    }
    if edges:
        buffer["edges_services"] = [{"ip": edge.ip, "port": edge.port} for edge in edges]
    return {
        "appid": creds.app_id,
        "client_ts": client_ts,
        "opid": opid,
        "sid": sid,
        "request_bodies": [{"uri": uri, "buffer": buffer}],
    }


def encode_request(payload: Mapping[str, object]) -> str:
    """The exact text sent in the form's ``request`` field."""
    return json.dumps(payload)


def _endpoint_url(host: str, proxy_server: str | None) -> str:
    if proxy_server is None:
        return f"{host}{AP_PATH}{AP_QUERY}"
    return f"https://{proxy_server}/ap/?url={urlsplit(host).netloc}{AP_PATH}{AP_QUERY}"


class AgoraAPClient:
    """Edge discovery against Agora's access points, trying each host in order.

    A borrowed ``session`` is never closed; without one the client creates its own on first use and
    closes it in ``close()`` / ``__aexit__``. TLS is verified unless ``verify_ssl=False`` (D10).
    ``id_factory``, when given, supplies the ``sid`` (when not passed) and then the ``opid`` of each
    request; by default they are drawn from ``secrets`` in the SDK's ranges.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        *,
        hosts: Sequence[str] = AP_HOSTS,
        verify_ssl: bool = True,
        timeout_s: float = AP_TIMEOUT_S,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], int] | None = None,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self._hosts = tuple(hosts)
        self._verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._clock = clock
        self._id_factory = id_factory

    def __repr__(self) -> str:
        return f"AgoraAPClient(hosts={list(self._hosts)!r}, verify_ssl={self._verify_ssl}, owns_session={self._owns_session})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the session this client created, if any; a borrowed session is left open."""
        if self._owns_session and self._session is not None:
            session, self._session = self._session, None
            await session.close()

    async def choose_server(
        self,
        creds: ChannelCredentials,
        *,
        role: int = ROLE_HOST,
        service_ids: Sequence[int] = DEFAULT_SERVICE_IDS,
        sid: str | None = None,
        proxy_server: str | None = None,
    ) -> APResponse:
        """Ask for gateway (and by default TURN) edges for ``creds`` (URI 22).

        Raises:
            APRejectedError: An AP answered and every requested service failed.
            APError: No host gave a usable answer; ``status`` is the last host's HTTP status, if any.

        """
        return await self._request(
            creds, uri=AP_URI_CHOOSE_SERVER, service_ids=service_ids, sid=sid, role=role, proxy_server=proxy_server
        )

    async def update_ticket(
        self,
        creds: ChannelCredentials,
        edges: Sequence[EdgeAddress],
        *,
        sid: str | None = None,
        service_ids: Sequence[int] = (SERVICE_GATEWAY,),
        proxy_server: str | None = None,
    ) -> APResponse:
        """Refresh the ticket for known ``edges`` (URI 28); raises as ``choose_server`` does."""
        return await self._request(
            creds, uri=AP_URI_UPDATE_TICKET, service_ids=service_ids, sid=sid, edges=edges, proxy_server=proxy_server
        )

    def _next_id(self, bound: int) -> int:
        return self._id_factory() if self._id_factory is not None else secrets.randbelow(bound)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(self, url: str, headers: Mapping[str, str], body: Mapping[str, object]) -> tuple[int, object]:
        """POST ``body`` as the multipart ``request`` field; returns the status and decoded JSON (``None`` if not JSON)."""
        form = aiohttp.FormData()
        form.add_field(_REQUEST_FIELD, encode_request(body), content_type="application/json")
        async with self._http().post(
            url, headers=headers, data=form, timeout=self._timeout, ssl=self._verify_ssl
        ) as response:
            text = await response.text(errors="replace")
        try:
            return response.status, json.loads(text) if text else None
        except (ValueError, RecursionError):
            return response.status, None

    async def _request(
        self,
        creds: ChannelCredentials,
        *,
        uri: int,
        service_ids: Sequence[int],
        sid: str | None,
        proxy_server: str | None,
        role: int = ROLE_HOST,
        edges: Sequence[EdgeAddress] = (),
    ) -> APResponse:
        sid = str(self._next_id(_SID_BOUND)) if sid is None else sid
        payload = build_request_payload(
            creds,
            uri=uri,
            service_ids=service_ids,
            sid=sid,
            opid=self._next_id(_OPID_BOUND),
            client_ts=int(self._clock() * 1000),
            role=role,
            edges=edges,
        )
        _LOGGER.debug(
            "AP request uri=%s channel=%s services=%s token=%s",
            uri,
            creds.channel_name,
            list(service_ids),
            fingerprint(creds.token),
        )
        status: int | None = None
        for host in self._hosts:
            url = _endpoint_url(host, proxy_server)
            try:
                status, data = await self._post(url, {}, payload)
            except (aiohttp.ClientError, TimeoutError) as exc:
                status = None
                _LOGGER.debug("AP host %s failed: %s", host, type(exc).__name__)
                continue
            if status != _HTTP_OK or not isinstance(data, dict):
                _LOGGER.debug("AP host %s answered HTTP %s with %s", host, status, type(data).__name__)
                continue
            try:
                return APResponse.from_api_response(data, clock=self._clock)
            except APRejectedError:
                raise
            except APError:
                _LOGGER.debug("AP host %s answered without a response_body", host)
        raise APError(f"no access point host answered usefully ({len(self._hosts)} tried)", status=status)
