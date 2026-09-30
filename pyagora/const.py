"""Hosts, protocol constants and timers; the only place a URL, service id or duration is spelled out.

Sources are noted per value: ``sdk`` means observed in Agora's Web SDK 4.24.x, ``shipped``
means the value the Mammotion or PetKit integration ran with in production.
"""

from __future__ import annotations

SDK_VERSION = "4.24.3"  # sdk: sent as sdk_version in join_v3

AP_HOSTS: tuple[str, ...] = (  # sdk: primary, then backup
    "https://webrtc2-ap-web-1.agora.io",
    "https://webrtc2-ap-web-2.agora.io",
    "https://webrtc2-ap-web-3.agora.io",
    "https://webrtc2-ap-web-4.agora.io",
)
AP_PATH = "/api/v2/transpond/webrtc"  # sdk

RTM_HOSTS: tuple[str, ...] = ("https://api.agora.io", "https://api.sd-rtn.com")  # shipped (PetKit)
RTM_PEER_MESSAGES_PATH = "/dev/v2/project/{app_id}/rtm/users/{user_id}/peer_messages"  # shipped (PetKit)

# AP request uris and service ids (docs/protocol.md §1)
AP_URI_CHOOSE_SERVER = 22
AP_URI_UPDATE_TICKET = 28
SERVICE_GATEWAY = 11
SERVICE_TURN = 26
DEFAULT_SERVICE_IDS: tuple[int, ...] = (SERVICE_GATEWAY, SERVICE_TURN)
AP_FLAG_GATEWAY = 4096  # the response block whose scalars (ticket, uid, cid) are primary
AP_FLAG_TURN = 4194310
AP_QUERY = "?v=2"  # sdk
EDGE_DOMAIN_SUFFIX = ".edge.agora.io"  # sdk: wss://<ip-dashed>.edge.agora.io:<port>
TURN_PORT = 3478  # sdk
TURNS_PORT = 443  # sdk
DEFAULT_AREA_CODE = "CN,GLOBAL"  # sdk
ROLE_HOST = 1  # shipped: AP detail 17, the client role

GATEWAY_TURN_PORT_OFFSET = 30  # sdk: serversFromGateway uses the gateway port + 30

# Timers, in seconds
AP_TIMEOUT_S = 10.0
GATEWAY_CONNECT_TIMEOUT_S = 10.0
JOIN_TIMEOUT_S = 15.0
GATEWAY_SEND_TIMEOUT_S = 5.0  # bounds the join and leave sends on a stalled socket
PING_INTERVAL_S = 3.0  # sdk
RENEW_TOKEN_DEBOUNCE_S = 30.0  # shipped (Mammotion); D8
KEEPALIVE_INTERVAL_S = 3.0  # shipped (Mammotion 4G FPV); D15
PEER_REJOIN_DEBOUNCE_S = 2.0  # shipped; D14
PEER_RECOVER_COOLDOWN_S = 15.0  # shipped
PEER_RECOVER_MAX_ATTEMPTS = 5  # shipped
PEER_RECOVER_RESET_S = 600.0  # shipped
DECLARED_SSRC_TIMEOUT_S = 15.0  # shipped (PetKit): answer without a remote SSRC after this
RTM_TIMEOUT_S = 10.0

DEFAULT_CLIENT_CODEC = "vp8"  # shipped (Mammotion); PetKit uses h264
DEFAULT_ORTC_DTLS_ROLE = "server"  # shipped (HA-Luba); D4
