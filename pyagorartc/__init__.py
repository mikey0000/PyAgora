"""pyagorartc: an async client for Agora RTC signalling.

``__all__`` is the supported surface (Constitution §10).
"""

from __future__ import annotations

from pyagorartc.ap import AgoraAPClient, APResponse
from pyagorartc.exceptions import (
    APError,
    APRejectedError,
    GatewayConnectError,
    JoinRejectedError,
    JoinTimeoutError,
    PyAgoraRTCError,
    RtmError,
    SdpError,
    SessionClosedError,
)
from pyagorartc.models import (
    ChannelCredentials,
    ChannelEncryption,
    CloseReason,
    EdgeAddress,
    IceCandidate,
    ICEServer,
    RemoteStream,
    RtmCredentials,
    SessionOptions,
    TurnCredentialStrategy,
    TurnMode,
    fingerprint,
)
from pyagorartc.rtm import RtmRestClient
from pyagorartc.sdp import extract_inline_candidates, filter_candidates, parse_trickle_fragment
from pyagorartc.session import AgoraSession

__all__ = [
    "APError",
    "APRejectedError",
    "APResponse",
    "AgoraAPClient",
    "AgoraSession",
    "ChannelCredentials",
    "ChannelEncryption",
    "CloseReason",
    "EdgeAddress",
    "GatewayConnectError",
    "ICEServer",
    "IceCandidate",
    "JoinRejectedError",
    "JoinTimeoutError",
    "PyAgoraRTCError",
    "RemoteStream",
    "RtmCredentials",
    "RtmError",
    "RtmRestClient",
    "SdpError",
    "SessionClosedError",
    "SessionOptions",
    "TurnCredentialStrategy",
    "TurnMode",
    "extract_inline_candidates",
    "filter_candidates",
    "fingerprint",
    "parse_trickle_fragment",
]
