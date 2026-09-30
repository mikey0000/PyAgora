"""The ``GatewayTransport`` seam: what the session needs from a WebSocket, and nothing more.

The ``websockets`` adapter lives beside it; the unit tier replaces it with a scripted fake.
"""

from __future__ import annotations

from contextlib import suppress
import logging
import ssl
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException
from websockets.protocol import State

from pyagora.exceptions import GatewayConnectError

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection


class GatewayConnection(Protocol):
    """One open gateway socket: text frames in and out, and a close."""

    async def send(self, text: str) -> None:
        """Send one text frame; raises ``GatewayConnectError`` if the socket is gone."""
        ...

    async def recv(self) -> str:
        """Return the next text frame; raises ``GatewayConnectError`` when the socket closes."""
        ...

    async def close(self) -> None:
        """Close the socket; idempotent."""
        ...

    @property
    def is_open(self) -> bool:
        """Whether frames can still be sent."""
        ...


class GatewayTransport(Protocol):
    """Opens gateway sockets. ``url`` is the full ``wss://`` URL for one edge."""

    async def connect(self, url: str, *, timeout_s: float, verify_ssl: bool) -> GatewayConnection:
        """Open one connection.

        Raises:
            GatewayConnectError: The socket could not be opened in time.

        """
        ...


def _ssl_context(*, verify: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


# Built at import: loading the CA bundle is blocking I/O that must not run on the event loop.
_VERIFIED_CONTEXT = _ssl_context(verify=True)
_UNVERIFIED_CONTEXT = _ssl_context(verify=False)
_SOCKET_ERRORS = (WebSocketException, OSError)
# websockets logs every frame at DEBUG, and a renew_token frame is short enough to carry its whole token (§6).
_WIRE_LOGGER = logging.getLogger("pyagora.session.wire")
_WIRE_LOGGER.setLevel(logging.INFO)


class WebsocketsConnection:
    """``GatewayConnection`` over a ``websockets`` client connection."""

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    async def send(self, text: str) -> None:
        """Send one text frame; raises ``GatewayConnectError`` if the socket is gone."""
        try:
            await self._connection.send(text)
        except _SOCKET_ERRORS as exc:
            raise GatewayConnectError("gateway socket closed") from exc

    async def recv(self) -> str:
        """Return the next frame as text; raises ``GatewayConnectError`` when the socket closes."""
        try:
            data = await self._connection.recv()
        except _SOCKET_ERRORS as exc:
            raise GatewayConnectError("gateway socket closed") from exc
        return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data

    async def close(self) -> None:
        """Close the socket; idempotent."""
        with suppress(*_SOCKET_ERRORS):
            await self._connection.close()

    @property
    def is_open(self) -> bool:
        """Whether frames can still be sent."""
        return self._connection.state is State.OPEN


def ssl_for(url: str, *, verify_ssl: bool) -> ssl.SSLContext | None:
    """The TLS context for ``url``: none for ``ws://`` (the loopback fake gateway), verified unless told not (D10)."""
    if urlsplit(url).scheme == "ws":
        return None
    return _VERIFIED_CONTEXT if verify_ssl else _UNVERIFIED_CONTEXT


class WebsocketsTransport:
    """``GatewayTransport`` backed by ``websockets``; TLS is verified unless ``verify_ssl`` is False (D10)."""

    async def connect(self, url: str, *, timeout_s: float, verify_ssl: bool) -> GatewayConnection:
        """Open one gateway socket.

        Raises:
            GatewayConnectError: The socket could not be opened within ``timeout_s``.

        """
        try:
            connection = await ws_connect(
                url, ssl=ssl_for(url, verify_ssl=verify_ssl), open_timeout=timeout_s, logger=_WIRE_LOGGER
            )
        except (TimeoutError, *_SOCKET_ERRORS) as exc:
            raise GatewayConnectError(f"could not open the gateway socket at {url}") from exc
        return WebsocketsConnection(connection)
