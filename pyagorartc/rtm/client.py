"""Agora RTM peer messages over the REST API: request shape, endpoint rotation and ack semantics (D18)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from typing import TYPE_CHECKING, Self
from urllib.parse import quote

import aiohttp

from pyagorartc.const import RTM_HOSTS, RTM_PEER_MESSAGES_PATH, RTM_TIMEOUT_S
from pyagorartc.exceptions import RtmError
from pyagorartc.models import fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import TracebackType

    from pyagorartc.models import RtmCredentials

_LOGGER = logging.getLogger(__name__)

DEFAULT_ACCEPTED_CODES: frozenset[str] = frozenset({"message_sent", "message_delivered"})
_HTTP_OK = 200
_HTTP_NOT_FOUND = 404
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500


def _is_rotating_status(status: int) -> bool:
    return status in {_HTTP_NOT_FOUND, _HTTP_TOO_MANY_REQUESTS} or status >= _HTTP_SERVER_ERROR


class RtmRestClient:
    """Sends RTM peer messages from ``creds.user_id`` to ``creds.peer_user_id``.

    The message vocabulary (what goes in ``payload``, how often) belongs to the host. A
    borrowed ``session`` is never closed; one the client creates is closed by ``close()``.
    """

    def __init__(
        self,
        creds: RtmCredentials,
        session: aiohttp.ClientSession | None = None,
        *,
        hosts: Sequence[str] = RTM_HOSTS,
        verify_ssl: bool = True,
        timeout_s: float = RTM_TIMEOUT_S,
    ) -> None:
        self._creds = creds
        self._session = session
        self._owns_session = session is None
        self._hosts = tuple(hosts)
        self._verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._preferred_host: str | None = None
        self._send_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"RtmRestClient(creds={self._creds!r}, hosts={self._hosts!r}, preferred_host={self._preferred_host!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()

    def update_token(self, token: str) -> None:
        """Use ``token`` from the next request on; RTM tokens rotate while a client lives."""
        self._creds = dataclasses.replace(self._creds, token=token)
        _LOGGER.info("RTM token updated (fp=%s)", fingerprint(token))

    async def close(self) -> None:
        """Close the HTTP session if this client created it."""
        if self._owns_session and self._session is not None:
            session, self._session = self._session, None
            await session.close()

    async def send_peer_message(
        self,
        payload: Mapping[str, object],
        *,
        wait_for_ack: bool = True,
        accepted_codes: frozenset[str] = DEFAULT_ACCEPTED_CODES,
    ) -> str:
        """Send ``payload`` (serialised to a compact JSON string) to the peer and return the ack code.

        A 404, 429, 5xx, connection error or timeout moves to the next host; the host that
        last succeeded is tried first on later calls.

        Raises:
            RtmError: every host failed (``status`` is the last HTTP status, or ``None`` if the
                last host was unreachable), the server refused the request with another status,
                or it answered 200 with a result or ``code`` outside ``accepted_codes`` (compared case-insensitively).
        """
        creds = self._creds
        path = RTM_PEER_MESSAGES_PATH.format(app_id=creds.app_id, user_id=quote(creds.user_id, safe=""))
        query = "?wait_for_ack=true" if wait_for_ack else ""
        headers = {
            "Content-Type": "application/json",
            "x-agora-token": creds.token,
            "x-agora-uid": creds.user_id,
            "Authorization": f"agora token={creds.token}",
        }
        body = {
            "destination": creds.peer_user_id,
            "enable_offline_messaging": False,
            "enable_historical_messaging": False,
            "payload": json.dumps(payload, separators=(",", ":")),
        }
        accepted = frozenset(code.lower() for code in accepted_codes)
        last_status: int | None = None
        async with self._send_lock:
            for host in self._iter_endpoints():
                url = f"{host}{path}{query}"
                try:
                    status, data = await self._post(url, headers, body)
                except (aiohttp.ClientError, TimeoutError) as exc:
                    _LOGGER.debug("RTM POST %s failed: %s", url, type(exc).__name__)
                    last_status = None
                    continue
                if _is_rotating_status(status):
                    _LOGGER.debug("RTM POST %s -> %s, trying next host", url, status)
                    last_status = status
                    continue
                return self._accept(url, status, data, accepted, host)
        raise RtmError("RTM peer message failed on every host", status=last_status)

    def _accept(self, url: str, status: int, data: object, accepted_codes: frozenset[str], host: str) -> str:
        if status != _HTTP_OK:
            raise RtmError(f"RTM peer message refused with HTTP {status}", status=status)
        fields = data if isinstance(data, dict) else {}
        result = str(fields.get("result", "")).lower()
        code = str(fields.get("code", "")).lower()
        _LOGGER.debug("RTM POST %s -> %s result=%s code=%s", url, status, result, code)
        if result != "success" or code not in accepted_codes:
            raise RtmError(
                f"RTM peer message not accepted: result={result} code={code}", status=status, code=code or None
            )
        self._preferred_host = host
        return code

    def _iter_endpoints(self) -> list[str]:
        """The hosts in order, the last one that succeeded first."""
        hosts = list(self._hosts)
        if self._preferred_host in hosts:
            hosts.remove(self._preferred_host)
            hosts.insert(0, self._preferred_host)
        return hosts

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(self, url: str, headers: Mapping[str, str], body: Mapping[str, object]) -> tuple[int, object]:
        """POST one request; returns the status and the decoded JSON body (``None`` if not JSON)."""
        async with self._http().post(
            url, headers=headers, json=body, timeout=self._timeout, ssl=self._verify_ssl
        ) as response:
            text = await response.text(errors="replace")
        try:
            return response.status, json.loads(text) if text else None
        except (ValueError, RecursionError):
            return response.status, None
