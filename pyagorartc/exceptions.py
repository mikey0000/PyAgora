"""The exception hierarchy; nothing else in the package defines an exception type.

Each type names a distinct recovery (``docs/architecture.md`` §2). Messages describe the
condition; no exception ever carries a token, ticket, credential or key.
"""

from __future__ import annotations


class PyAgoraRTCError(Exception):
    """Base class for every error raised by this library."""


class APError(PyAgoraRTCError):
    """Edge discovery failed: every access point host was tried and none answered usefully."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class APRejectedError(APError):
    """The access point answered, but every requested service came back with a non-zero code."""

    def __init__(self, codes: dict[int, int]) -> None:
        super().__init__(f"access point rejected every service: {codes}")
        self.codes = codes


class SdpError(PyAgoraRTCError):
    """An SDP offer could not be turned into ORTC, or an answer could not be built from the gateway ORTC."""


class GatewayConnectError(PyAgoraRTCError):
    """The gateway WebSocket could not be opened on any edge."""


class JoinRejectedError(PyAgoraRTCError):
    """The gateway answered the join with a failure result."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"join rejected (code={code}): {message}")
        self.code = code
        self.message = message


class JoinTimeoutError(PyAgoraRTCError):
    """The gateway did not answer the join within the configured timeout."""


class SessionClosedError(PyAgoraRTCError):
    """An operation was attempted on a session that has already ended."""


class RtmError(PyAgoraRTCError):
    """An RTM peer message could not be delivered on any endpoint."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
