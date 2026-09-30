# Migration

How each known host stops carrying its own `agora_*` modules and depends on
`pyagorartc` instead. Constitution §10 requires this file to be current before
any release that changes the public surface.

The hosts today:

| Host | Local Agora code | Entry points |
|---|---|---|
| Mammotion (`HA-Luba/custom_components/mammotion/`) | none since the `pyagorartc-migration` branch (§2) | `camera.py`, `stream_session.py`, `coordinator.py::async_check_stream_expiry` |
| PetKit (`homeassistant_petkit/custom_components/petkit/`) | `agora_api.py`, `agora_sdp.py`, `agora_websocket.py`, `agora_rtm.py`, parts of `camera.py`, `webrtc_common.py` and `whep_proxy.py` | `whep_proxy.py::PetkitAgoraUpstreamManager`, `camera.py` |

Names below are the target API (`docs/analysis/divergence.md` §6, with
`SessionOptions` in `pyagorartc/models.py` and the RTM client as its tests pin
it). Where that API is still unsettled, this file says so.

## 1. Common to both hosts

### 1.1 The shape of a stream

```
vendor call              → ChannelCredentials            (host)
AgoraAPClient.choose_server(creds) → APResponse          (HTTP)
APResponse.get_ice_servers(...) → [ICEServer]            → host converts for HA
AgoraSession(creds, ap, options=..., callbacks...)       one per offer
session.add_ice_candidate(...)                           before join only (D11)
answer = await session.join(offer_sdp, session_id)       raises on failure (D9)
on_closed(CloseReason) / on_peer_left(uid)               from the session
await session.close()                                    leave, cancel, await
```

A session is single-use (architecture §2). A new offer is a new
`AgoraSession`. There is no `connect_and_join` on a long-lived handler, no
`handler.candidates = []` reset, and no re-entrancy guard to rely on.

### 1.2 ICE servers

`APResponse.get_ice_servers(...)` returns `pyagorartc.ICEServer`. Home
Assistant wants `webrtc_models.RTCIceServer`. The host converts; the
library does not import `webrtc_models` (D19).

```python
from webrtc_models import RTCIceServer

def to_rtc_ice_servers(ap: APResponse) -> list[RTCIceServer]:
    return [
        RTCIceServer(urls=s.urls, username=s.username, credential=s.credential)
        for s in ap.get_ice_servers(use_all_turn_servers=False)
    ]
```

`use_all_turn_servers=False` is what both hosts pass today (three entries:
udp, tcp, turns for the first TURN edge; PetKit `camera.py:638`). The TURN
credential is the uid-derived one by default (D12).
`strategy=TurnCredentialStrategy.DETAIL_FIRST` restores PetKit's old order,
detail `8`/`4` then the uid pair (`agora_api.py:107-116`; Q5).

### 1.3 Candidates

HA hands the host `webrtc_models.RTCIceCandidateInit`. The session takes
`pyagorartc.IceCandidate`.

```python
def to_ice_candidate(c: RTCIceCandidateInit) -> IceCandidate:
    return IceCandidate(c.candidate, sdp_mid=c.sdp_mid, sdp_mline_index=c.sdp_m_line_index)
```

Only candidates added before `join()` sends the join frame reach Agora
(D11). The gateway is ice-lite and its own candidates are in the answer, so
a viewer that trickles everything later still connects. There is no gateway
message for post-join trickle (Q4).

Pure helpers replace the hosts' copies:

| Helper | Replaces |
|---|---|
| `extract_inline_candidates(offer_sdp) -> list[IceCandidate]` | PetKit's inline `a=candidate:` loop (`whep_proxy.py:163-168`) and the unused `webrtc_common._add_offer_candidates`; `join()` already sends these, so a host calls it only to inspect or filter an offer (§3.6) |
| `filter_candidates(candidates, turn_ips) -> list[IceCandidate]` | PetKit `camera.PetkitWebRTCCamera._filter_candidates` (`camera.py:654-671`), same rule: keep srflx/prflx and relays on a TURN ip, else return the input |
| `parse_trickle_fragment(fragment) -> list[IceCandidate]` | PetKit `whep_proxy._parse_trickle_candidates` (`whep_proxy.py:509-541`); verbatim lines instead of a rebuild from `sdp_transform` fields |

### 1.4 Errors

`join()` never returns `None` and never returns a made-up SDP (D9). Every
failure is a typed exception under `PyAgoraRTCError` (Constitution §5).

| Exception | Raised by | Host reaction |
|---|---|---|
| `APError` | `choose_server` | No edge. Tell the viewer; retry on the next offer. |
| `APRejectedError` (an `APError`) | `choose_server` | Every service came back non-zero; `.codes` says which. Usually a bad or expired token: refetch vendor credentials. |
| `SdpError` | `join` | The viewer's offer could not be converted. Not retryable with the same offer. |
| `GatewayConnectError` | `join` | No edge accepted the socket, or it closed (or stalled) before the join result. Retryable. |
| `JoinRejectedError` | `join` | The gateway refused the join; `.code` is its code. Often the token. |
| `JoinTimeoutError` | `join` | No join result in `join_timeout_s`. Retryable. |
| `SessionClosedError` | any call after the end | Programming error in the host: make a new session. |
| `RtmError` | `RtmRestClient.send_peer_message` | `.status` / `.code`; the peer did not accept the message. |

`asyncio.CancelledError` is re-raised, never swallowed (D1).

### 1.5 Callbacks

| Callback | Signature | Fires when |
|---|---|---|
| `token_provider` | `async () -> str \| None` | on `on_token_privilege_will_expire`, at most once per 30 s (D8, Q11). `None` from it, or no provider, resends the last token sent. |
| `on_peer_left` | `async (uid: int) -> None` | a publisher left and did not come back within `PeerRecovery`'s debounce, cooldown and attempt cap (D14). |
| `on_closed` | `async (reason: CloseReason) -> None` | once, whatever ended the session (D14). |
| `keepalive` | `async () -> bool` | every `keepalive_interval_s` s while joined; `False` stops it (D15). |
| `on_stream` | `async (stream: RemoteStream) -> None` | once per subscription, from the subscribe task right after the first `subscribe` for that `(uid, ssrc)` is sent (a publisher that leaves and returns is subscribed, and reported, again). |
| `spawn` | `(coro) -> asyncio.Task` | every owned background task is created through it (D13). |

Read-only state for polling hosts: `is_connected` (socket open, not
ended), `is_joined`, `remote_users` (publishers present that pass
`target_uid`) and `remote_streams` (their announced video streams, in
arrival order).

`on_closed` replaces Mammotion's `session_ended` and PetKit's
`on_connection_lost`. Reasons: `GATEWAY_QUIT` (the gateway's `quit`
notification, e.g. 2003 repeat join), `P2P_LOST`, `SOCKET_CLOSED`,
`DEADLINE`, `CLOSED_BY_HOST`, `JOIN_FAILED`. A host that tears its own state
down from `on_closed` should return early on `CLOSED_BY_HOST` (it is already
in its own close path) and on `JOIN_FAILED` (`join()` is about to raise to it,
D23), and should tear down only the state that belongs to *this* session:
by then a newer one may hold the slot (§2.4, §3.4).

In Home Assistant, pass `spawn` so HA tracks the tasks:

```python
spawn=lambda coro: hass.async_create_background_task(coro, f"{DOMAIN} agora {device_id}")
```

### 1.6 Dependencies

| Package | pyagorartc needs | Note |
|---|---|---|
| `aiohttp` | `>=3.10` | HA provides it. Pass `async_get_clientsession(hass)` to `AgoraAPClient` and `RtmRestClient`; the library never closes a session it did not create (Constitution §4). |
| `websockets` | `>=13.1` | See below. |
| `sdp-transform` | `>=1.1.0` | The one SDP parser (D3). Hosts drop their own pin. |
| `webrtc-models` | not needed (D19) | HA core pins `webrtc-models==0.3.0` and hosts keep using it for HA's API. |

**Why the `websockets` floor is 13.1.** The session uses only the asyncio
client (`websockets.asyncio.client.connect`), which exists since 13.0
(D19). Home Assistant core does not pin an exact version; its
`package_constraints.txt` carries `websockets>=15.0.1` under "Prevent
accidental fallbacks". HA 2026.9.3 installs 16.0. A library floor below
HA's floor never conflicts. An exact pin in an integration manifest does:
PetKit's `websockets==15.0.1` fights HA as soon as core moves to 16. So the
hosts drop their pins and let HA's constraint decide.

## 2. Mammotion (HA-Luba)

### 2.1 What moves where

| Today (`custom_components/mammotion/`) | Becomes |
|---|---|
| `agora_api.AgoraAPIClient` | `pyagorartc.AgoraAPClient` |
| `agora_api.AgoraAPIClient.choose_server(app_id=, token=, channel_name=, user_id=, service_flags=)` | `choose_server(creds)`; the default service ids are `(11, 26)` (`const.DEFAULT_SERVICE_IDS`) |
| `agora_api.AgoraResponse` | `pyagorartc.APResponse` |
| `agora_api.SERVICE_IDS` | not needed (defaults); `pyagorartc.const.SERVICE_GATEWAY` / `SERVICE_TURN` if spelled out |
| `agora_api.EdgeAddress`, `ICEServer` | `pyagorartc.EdgeAddress`, `pyagorartc.ICEServer` |
| `agora_api.update_ticket`, `get_turn_server_config` (no caller) | `AgoraAPClient.update_ticket`, `APResponse.turn_server_config` (kept, still no caller) |
| `agora_sdp.parse_offer_to_ortc`, `SDPParser`, `generate_answer_from_ortc` | internal to `AgoraSession` (`sdp.offer_to_ortc`, `sdp.answer_from_ortc`); the dead writer is gone |
| `agora_websocket.AgoraWebSocketHandler(hass, recover_stream=, keepalive=, target_uid=, session_ended=)` | `pyagorartc.AgoraSession(creds, ap, options=, on_peer_left=, on_closed=, keepalive=, deadline=, spawn=)`, one per offer |
| `handler.connect_and_join(stream_data, offer, session_id, agora_response)` | `await session.join(offer, session_id)` |
| `handler.candidates.append(c)` / `handler.candidates = []` | `session.add_ice_candidate(to_ice_candidate(c))`; a new session per offer |
| `handler.disconnect()` | `await session.close()` |
| `_fpv_keepalive_loop(availableTime)` | `keepalive=` + `deadline=` (D15) |
| `_schedule_peer_recovery` / `_peer_recovery`, `PEER_*` constants | `session/recovery.py::PeerRecovery` → `on_peer_left(uid)` |
| `_handle_notification` quit → `session_ended` | `on_closed(CloseReason.GATEWAY_QUIT)` |
| renew-token debounce | inside the session (D8) |
| `_handle_join_success` fingerprint injection from `agora_response.addresses` | inside the session, only when the gateway sends none (D26) |
| fallback SDP generator | deleted (D9) |
| `camera.py`: `_fpv_keepalive`, `_recover_stream`, `_async_session_ended`, `get_ice_servers`, teardown, services | stays (host glue); signatures adapt below |
| `coordinator.py::async_check_stream_expiry` (token cache, all-camera fallback, 50504) | stays; its AP block shrinks to one `async_choose_server` call (§2.3) |
| credential mapping, AP call, `RTCIceServer` and candidate conversion | new host module `stream_session.py`: `mammotion_credentials`, `async_choose_server`, `to_rtc_ice_servers`, `to_ice_candidate` (§1.2, §1.3, §2.2, §2.3) |
| `tests_ha/test_agora_answer_sdp.py`, `test_agora_camera_uid_filter.py`, `test_agora_peer_recovery_cap.py`, `test_agora_renew_token_debounce.py`, `test_agora_session_quit.py`, `test_fpv_keepalive.py` | deleted from the host; the library covers them (below) |

The six deleted host tests and where the library pins the same behaviour:

| Deleted host test | Library test |
|---|---|
| `test_agora_answer_sdp.py` | `tests/unit/sdp/test_answer.py` |
| `test_agora_camera_uid_filter.py` | `tests/unit/session/test_session_recovery.py` (`target_uid`) |
| `test_agora_peer_recovery_cap.py` | `tests/unit/session/test_recovery.py`, `test_session_recovery.py` |
| `test_agora_renew_token_debounce.py` | `tests/unit/session/test_session_timers.py` |
| `test_agora_session_quit.py` | `tests/unit/session/test_session.py` (`GATEWAY_QUIT`) |
| `test_fpv_keepalive.py` | `tests/unit/session/test_session_timers.py` |

The host keeps its own tests for the glue: `tests_ha/test_camera_agora_session.py` and
`test_stream_session.py`, on a hand-written `AgoraSession` stand-in in
`tests_ha/agora_session_support.py`.

### 2.2 Credentials

`StreamSubscriptionResponse` (`pymammotion/http/model/camera_stream.py`)
maps to `ChannelCredentials` (D2):

| `StreamSubscriptionResponse` | `ChannelCredentials` / session |
|---|---|
| `appid` | `app_id` |
| `channelName` | `channel_name` |
| `token` | `token` |
| `uid` | `uid` (the viewer uid; every camera token shares it) |
| `license` | `license` (now sent in the join, D7) |
| `openEncrypt`, `key`, `salt` | `encryption=ChannelEncryption(...)`, salt base64-decoded (D20) |
| `availableTime` | not a credential: `deadline=` on the session (D2, D15) |
| `cameras[]` | not used: the camera is `SessionOptions.target_uid` = slot + 1 (D2) |
| `areaCode` | leave at the default `"CN,GLOBAL"` for now; see below |

Put this in `stream_session.py`, with the other conversions (§2.3).

```python
import base64

from pyagorartc import ChannelCredentials, ChannelEncryption
from pymammotion.http.model.camera_stream import StreamSubscriptionResponse


def mammotion_credentials(data: StreamSubscriptionResponse) -> ChannelCredentials:
    encryption = None
    if data.openEncrypt and data.key:
        encryption = ChannelEncryption(
            mode="aes-256-gcm2",  # the APK enables AES_256_GCM2 whenever openEncrypt is set
            secret=data.key,
            salt=base64.b64decode(data.salt) if data.salt else None,
        )
    return ChannelCredentials(
        app_id=data.appid,
        channel_name=data.channelName,
        token=data.token,
        uid=int(data.uid),
        license=data.license,
        encryption=encryption,
    )
```

- **Encryption caveat (D20).** The library sends no `aes_*` field in the
  join (the SDK RSA-wraps the secret, Q17) and logs a WARNING when
  `encryption` is set; it cannot decrypt media either (architecture §6).
  An encrypted channel will not show a picture. HA-Luba chose to warn:
  `_new_session` logs a WARNING whenever `openEncrypt` is non-zero, and
  the library logs its own when `key` is also set. Q6 asks whether any
  mower sets it.
- **Area code.** The captured `areaCode` is `AREA_CODE_EU`, an Android SDK
  enum name. Whether the Web AP accepts that form is unverified (Q12).
  Keep the default until a capture shows the mapping.

### 2.3 Edge discovery and ICE servers (`stream_session.py`, `coordinator.py`)

`stream_session.py` is the one home for the host's Agora conversions:
`mammotion_credentials` (§2.2), `async_choose_server`, `to_rtc_ice_servers`
(§1.2) and `to_ice_candidate` (§1.3). The coordinator and the camera import
from it; neither builds an `AgoraAPClient` or an `RTCIceServer` itself.

```python
async def async_choose_server(hass: HomeAssistant, data: StreamSubscriptionResponse) -> APResponse:
    client = AgoraAPClient(session=async_get_clientsession(hass))
    return await client.choose_server(mammotion_credentials(data))
```

No `async with`: the session is HA's, and a borrowed session is never
closed, so the context manager does nothing (Constitution §4).

The `AgoraAPIClient` block in `async_check_stream_expiry` becomes:

```python
if stream_data is not None and stream_data.data is not None:
    try:
        agora_response = await async_choose_server(self.hass, stream_data.data)
    except PyAgoraRTCError:
        LOGGER.exception("Agora edge discovery failed")
        self.ice_servers = []
    else:
        self.ice_servers = to_rtc_ice_servers(agora_response)
        self._agora_response = agora_response
```

`camera.async_setup_entry` converts too: it calls `async_check_stream_expiry()`
once for the first mower and sets `to_rtc_ice_servers(agora_response)` on every
mower's coordinator. Both call sites use the same helper.

The broad `except Exception` narrows to `PyAgoraRTCError`; anything else
reaches the token refresh's outer catch as before. The AP call is now
TLS-verified (D10); `AgoraAPClient(..., verify_ssl=False)` exists for
proxied networks.

### 2.4 The session (`camera.py`)

The entity holds at most one session, created per offer. This is the
shape the branch ships (trimmed).

```python
import time

from pyagorartc import AgoraSession, APResponse, CloseReason, IceCandidate, PyAgoraRTCError, SessionOptions

from .stream_session import mammotion_credentials, to_ice_candidate


class MammotionWebRTCCamera(MammotionCameraBaseEntity):
    def __init__(self, ...) -> None:
        ...
        self._session: AgoraSession | None = None
        self._pending_offer_id: str | None = None      # the offer being negotiated
        self._early_candidates: list[IceCandidate] = []

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message) -> None:
        # ... 409 when self._join_lock is held ...
        async with self._join_lock:
            self._pending_offer_id = session_id
            self._early_candidates = []
            self._sessions[session_id] = send_message
            await self.coordinator.async_register_camera_session(self.entity_description.key)
            answered = False
            try:
                answered = await self._async_answer_offer(offer_sdp, session_id, send_message)
            finally:
                self._pending_offer_id = None
                if not answered:
                    self.close_webrtc_session(session_id)
                elif not self._sessions:
                    await self.async_close_webrtc_session()  # the viewer left mid-negotiation

    async def _async_answer_offer(self, offer_sdp, session_id, send_message) -> bool:
        stream_data, ap = await self.coordinator.async_check_stream_expiry(force=True)
        await self.coordinator.async_send_command("send_todev_ble_sync", sync_type=3)
        if not stream_data:
            send_message(WebRTCError("500", "No stream data available for WebRTC offer"))
            return False
        if self.entity_description.target_uid != 1 and not self.coordinator.all_cameras_streaming:
            send_message(WebRTCError("503", "Vision stream unavailable"))
            return False
        if ap is None:
            send_message(WebRTCError("500", "No Agora edge available for WebRTC offer"))
            return False
        await self._async_close_session()  # sessions are single-use; the old keep-alive must stop
        session = self._new_session(stream_data, ap)
        for candidate in self._early_candidates:
            session.add_ice_candidate(candidate)
        self._early_candidates = []
        self._session = session
        try:
            answer = await session.join(offer_sdp, session_id)
        except PyAgoraRTCError as err:
            send_message(WebRTCError("500", f"WebRTC negotiation failed: {err}"))
            return False  # the caller's finally closes the session
        send_message(WebRTCAnswer(answer))
        return True

    def _new_session(self, data: StreamSubscriptionResponse, ap: APResponse) -> AgoraSession:
        if data.openEncrypt:
            _LOGGER.warning("Stream token has openEncrypt=%s; expect no picture", data.openEncrypt)  # D20, Q6
        deadline = None
        if self.coordinator.is_on_4g and data.availableTime and data.availableTime > 0:
            deadline = time.monotonic() + data.availableTime
        return AgoraSession(
            mammotion_credentials(data),
            ap,
            options=SessionOptions(client_codec="vp8", target_uid=self.entity_description.target_uid),
            on_peer_left=self._on_peer_left,
            on_closed=self._on_closed,
            keepalive=self._fpv_keepalive,      # MQTT refresh_fpv on 4G; returns False on WiFi
            deadline=deadline,
            spawn=lambda coro: self.hass.async_create_background_task(coro, f"{DOMAIN} agora {self.entity_id}"),
        )

    async def async_on_webrtc_candidate(self, session_id, candidate) -> None:
        if session_id != self._pending_offer_id:
            return  # not the offer being negotiated; Agora has no trickle message (Q4)
        self._early_candidates.append(to_ice_candidate(candidate))

    async def async_close_webrtc_session(self) -> None:
        await self._async_close_session()
        await self.coordinator.async_release_camera_session(self.entity_description.key)

    async def _async_close_session(self) -> None:
        if (session := self._session) is not None:
            self._session = None
            await session.close()

    async def _on_peer_left(self, uid: int) -> None:
        await self._recover_stream()  # BLE sync + get_stream_subscription, unchanged

    async def _on_closed(self, reason: CloseReason) -> None:
        if reason in (CloseReason.CLOSED_BY_HOST, CloseReason.JOIN_FAILED):
            return
        viewers = list(self._sessions.values())
        if not viewers:
            return
        self._sessions.clear()
        message = {
            CloseReason.GATEWAY_QUIT: "Another camera on this mower took over the stream",
            CloseReason.DEADLINE: "4G streaming budget exhausted",
        }.get(reason, "Stream lost")
        for send_message in viewers:
            send_message(WebRTCError("503", message))
        await self.async_close_webrtc_session()
```

Why each piece is there:

| Piece | Reason |
|---|---|
| Checks run stream data → uid gate → AP response | No token is a 500; a vision camera without `all_cameras_streaming` is a 503 before any AP use; a missing AP response is a clean 500 rather than an `AttributeError` in the session. |
| `_async_close_session()` before `_new_session` | A session is single-use (architecture §2). Left running, the previous one's keep-alive keeps sending `refresh_fpv`. |
| `_pending_offer_id` / `_early_candidates` | Candidates are buffered for the offer being negotiated and added before `join` (D11). A candidate for any other offer id is dropped. One that arrives after `join` starts lands in the buffer and is discarded with it; the session would ignore it anyway (Q4). |
| `_on_closed` returns on `JOIN_FAILED` | The session fires `on_closed(JOIN_FAILED)` before `join()` raises (D9, D23). The 500 in `_async_answer_offer` already told the viewer; a 503 on top would be a second error. |
| `_on_closed` returns on `CLOSED_BY_HOST` | The host is already in its own close path (§1.5). |
| `_on_closed` returns with no viewer left | Nobody to tell, and `async_close_webrtc_session` already ran or will. |
| `keepalive_interval_s` not passed | The default (`const.KEEPALIVE_INTERVAL_S`, 3 s) is the shipped Mammotion cadence (D15). |

`async_teardown_stream` replaces `self._agora_handler.disconnect()` with the
same `session.close()`. `_fpv_keepalive` and `_recover_stream` are
unchanged. `_async_session_ended` becomes `_on_closed`. The
`websockets.exceptions.WebSocketException` / `json.JSONDecodeError` catch in
`_async_answer_offer` and the `(OSError, ValueError, TypeError)` catch in
`_perform_webrtc_negotiation` go: the library raises `PyAgoraRTCError`
subclasses (§1.4). `_perform_webrtc_negotiation` can be inlined.

`token_provider` stays unset: the gateway accepts the join token on renew
(D8), and minting a new Mammotion stream token restarts the mower's stream.

The `deadline` is computed only on 4G. The old loop enforced
`availableTime` only while `keepalive` returned `True`, which it never does
on WiFi. The library's deadline is independent of the keep-alive (D15), so
gating it on `is_on_4g` at join time keeps WiFi streams unbounded as they
were. See §2.5 for what that does to a mid-session network switch.

### 2.5 Behaviour changes Mammotion will see

| Change | Decision | What the host does |
|---|---|---|
| A failed join raises; no fabricated answer | D9 | Catch `PyAgoraRTCError`, send `WebRTCError` (§2.4). |
| TLS verified on the AP call and the gateway socket | D10 | Nothing, unless behind a TLS-intercepting proxy (`verify_ssl=False`). |
| Browser candidates known before join are sent in the join ORTC | D11 | Nothing. Candidates that arrive later are still not sent. |
| Join `attributes` nested under `userAttributes`; `enablePreallocPC: true`; `license` sent | D7 | Nothing. First session run confirms the gateway accepts `license`. |
| `on_p2p_lost` is ignored unless `SessionOptions(end_on_p2p_lost=True)` | D22 | Matches HA-Luba, which had the handler disabled; nothing to do. |
| Socket close ends the session with `SOCKET_CLOSED` | D14 | Previously the state changed silently; now viewers get a 503. |
| `availableTime` expiry calls `on_closed(DEADLINE)` | D15 | Previously the handler disconnected and nobody told the viewer or released the camera. |
| One session per offer | architecture §2 | Hold `self._session`; no handler in `__init__`. |
| A failed AP service block is skipped, not fatal | D1 | A TURN failure no longer kills gateway discovery. |
| Streams already listed in the join payload are subscribed | D1 | Nothing. |
| Answer falls back to the offer's payload types when the gateway lists no codec | D1 | Nothing (was invalid SDP). |
| AP detail-19 fingerprints are used only when the gateway ORTC has none | D26 | Nothing; the answer used the gateway's first fingerprint either way. |
| Background tasks are owned and awaited on close | D13 | Pass `spawn` (§1.5). |
| Unchanged: no `set_client_role` (D6), ORTC DTLS role `server` (D4), `a=setup` mirrors the gateway (D5), MID stripped from the answer (D16), `leave` on close, 30 s renew debounce (D8) | | |

Behaviour changes observed in the real migration (`pyagorartc-migration`):

| Change | Decision | Note |
|---|---|---|
| The 4G deadline is fixed at join time | D15 | Before, a mid-session switch to WiFi made `keepalive` return `False`, the loop exited, and `availableTime` stopped being enforced. Now the deadline still fires on a session that joined on 4G. A session that joined on WiFi still has none. |
| A failed AP call keeps the previous AP response | D1 | As before: `ice_servers` is cleared, `_agora_response` is not, so the next offer pairs the new token with the last good AP answer. |
| The D20 encryption WARNING fires per offer | D20, Q6 | `_new_session` logs one whenever `openEncrypt` is set; the library adds its own when `key` is also present. |

### 2.6 Manifest and requirements

`custom_components/mammotion/manifest.json`:

```json
"loggers": [
  "pymammotion",
  "pyagorartc"
],
"requirements": [
  "pymammotion==0.10.1",
  "pyagorartc==0.2.0"
]
```

`pyagorartc` in `loggers` lets HA's debug-logging toggle capture the
library, which the Q11 run needs (§4).

| File | Before | After |
|---|---|---|
| `manifest.json` `requirements` | `pymammotion` only | adds `pyagorartc` |
| `manifest.json` `loggers` | `pymammotion` | adds `pyagorartc` |
| `pyproject.toml` `dependencies` | `websockets>=15.0.1`, `sdp-transform>=1.1.0` | both dropped; `pyagorartc==0.2.0` added (it declares both, §1.6) |
| `uv.lock` | | regenerated with `uv lock` |

The manifest never pinned `websockets` or `sdp-transform`; only the dev
`pyproject.toml` did. HA-Luba's dev venv links `pymammotion` to a local
checkout, so run its tests with `uv run --no-sync` after locking, or the
sync replaces the link.

`pymammotion` still declares `sdp-transform>=1.1.0`, `websockets>=13.1` and
`webrtc-models>=0.3.0` and imports none of them. Drop them in its next
release (backlog). Until then they are harmless duplicates.

### 2.7 Checklist

Done on HA-Luba's `pyagorartc-migration` branch:

- [x] Add `pyagorartc` to `manifest.json` `requirements` and `loggers` (§2.6).
- [x] Add `stream_session.py` with `mammotion_credentials`,
      `async_choose_server`, `to_rtc_ice_servers`, `to_ice_candidate` (§2.3).
- [x] `coordinator.py`: replace the `AgoraAPIClient` block (§2.3); return
      `APResponse` from `async_check_stream_expiry`.
- [x] `camera.py`: `async_setup_entry` uses `to_rtc_ice_servers`; drop
      `AgoraWebSocketHandler`; add `_session`, `_pending_offer_id`,
      `_early_candidates`, `_new_session`, `_on_peer_left`, `_on_closed` (§2.4).
- [x] `openEncrypt != 0`: warn, do not refuse (D20).
- [x] Delete `agora_api.py`, `agora_sdp.py`, `agora_websocket.py` and the six
      Agora tests (§2.1).
- [x] Drop `websockets` and `sdp-transform` from `pyproject.toml`; `uv lock`.

Still open (backlog):

- [ ] Run one WiFi and one 4G session on a Luba 2 and a Yuka, every camera
      `target_uid`, before release (Q2, Q5, Q6, Q10, Q11; §4).
- [ ] Open a `pymammotion` change dropping its unused `sdp-transform`,
      `websockets` and `webrtc-models` requirements.

## 3. PetKit

Verified against `homeassistant_petkit` (manifest `1.27.0`) and `pypetkitapi`
1.29.0. File references below are relative to `custom_components/petkit/`.

### 3.1 What moves where

| Today (`custom_components/petkit/`) | Becomes |
|---|---|
| `agora_api.AgoraAPIClient`, `AgoraResponse`, `SERVICE_IDS`, `derive_password` | `pyagorartc.AgoraAPClient`, `pyagorartc.APResponse`. PetKit asks for services `11` and `26` (`camera.py:631-634`), which are the library defaults. |
| `agora_sdp.py` (`SDPParser`, `parse_offer_to_ortc`) | internal to `AgoraSession` |
| `agora_websocket.AgoraWebSocketHandler(rtc_token_provider=, prefer_instant_video=, subscribe_retry_delay=, subscribe_retry_attempts=, declare_remote_video_ssrc=, disable_audio_answer=, on_connection_lost=)` (`whep_proxy.py:154-162`) | `pyagorartc.AgoraSession(creds, ap, options=PETKIT_OPTIONS, token_provider=, on_closed=, spawn=)`, one per WHEP POST (§3.4) |
| `handler.connect_and_join(live_feed=, offer_sdp=, session_id=, app_id=, agora_response=)` (`whep_proxy.py:184-190`) | `await session.join(offer_sdp, session_id)`; the feed and `app_id` move into `ChannelCredentials` |
| the offer's `a=candidate:` loop and `camera.filter_agora_candidates` over it (`whep_proxy.py:163-173`) | nothing: `join()` sends the offer's inline candidates itself, **unfiltered** (§3.6) |
| `handler.add_ice_candidate` from the PATCH view (`whep_proxy.py:270-271`) | nothing: a PATCH always follows the join (§3.4) |
| `handler.disconnect()` | `await session.close()` |
| `camera._filter_candidates`, `filter_agora_candidates` (`camera.py:654-679`) | `filter_candidates(candidates, turn_ips)`, for the host's own use |
| `whep_proxy._parse_trickle_candidates` (`whep_proxy.py:509-541`) | `parse_trickle_fragment(fragment)`; it takes the last `sdp_transform` import with it (`whep_proxy.py:14`) |
| `webrtc_common._add_offer_candidates` (unused) | `extract_inline_candidates(offer_sdp)` |
| `webrtc_common._resolve_agora_user_id` (unused) | the uid fallback in §3.2 |
| fingerprint merge from the AP (`agora_websocket.py:357-391`) | inside the session, only when the gateway sends none (D26) |
| subscribe on announcement, the join-payload walk, the retry loop (`agora_websocket.py:440-472`, `555-646`) | inside the session. `existing_streams_from_join` is PetKit's walk; retries are ack-driven and a post-join stream waits for `on_user_online` (§3.6, Q18) |
| `agora_rtm.AgoraRTMSignaling._send_command`, `_iter_endpoints`, `_ensure_session`, `SIGNALING_DOMAINS`, `SIGNALING_PATHS`, `SUCCESS_CODES` | `pyagorartc.rtm.RtmRestClient.send_peer_message` (D18) |
| `AgoraRTMSignaling.start_live`, `_heartbeat_loop`, `stop_live`, `send_ptz_ctrl`, `update_tokens`, `STOP_SUCCESS_CODES` | stays (PetKit vocabulary, D18); rebuilt on `RtmRestClient` (§3.5) |
| `whep_proxy.PetkitAgoraUpstreamManager._refresh_tokens` (`whep_proxy.py:289-296`) | stays; the 20-minute RTM token loop (§3.5) |
| `webrtc_common._get_live_feed_for_webrtc`, `_missing_live_feed_fields`, `_live_feed_ready_for_webrtc`, the `TEMP_CAMERA_TYPES` wake (`webrtc_common.py:18-32`, `105-163`) | stays (host glue; no Agora code in it) |
| `whep_proxy.py` views, `_check_external_auth`, `PetkitGo2RTCProxyManager`; `go2rtc_stream.py` (no Agora import); the browser relay and `async_register_ice_servers` in `camera.py` | stays (host glue) |
| `const.AGORA_APP_ID` (`const.py:14`) | stays in `const.py`; becomes `ChannelCredentials.app_id` and `RtmCredentials.app_id` |

### 3.2 Credentials

`LiveFeed` (`pypetkitapi` 1.29.0, `containers.py:296-316`) plus `AGORA_APP_ID`
map to both credential types:

| Source | `ChannelCredentials` | `RtmCredentials` |
|---|---|---|
| `AGORA_APP_ID` | `app_id` | `app_id` |
| `channel_id` | `channel_name` | |
| `rtc_token` | `token` | |
| `uid` (`int \| None`) | `uid` (must be an `int`) | |
| `app_rtm_user_id` | | `user_id` |
| `dev_rtm_user_id` | | `peer_user_id` |
| `rtm_token` | | `token` |

**The uid.** pypetkitapi's validator fills `uid` from
`int(app_rtm_user_id.split("_")[1])` when the payload lacks it, and leaves it
`None` when that split fails. That is the only step it takes. The account user id
is the second tier of PetKit's own, unused `_resolve_agora_user_id`
(`webrtc_common.py:35-56`: feed uid, then `client._session.user_id`, then every
digit of `app_rtm_user_id`, then `0`). Today `camera.py:630` passes `live_feed.uid`
as is, so a `None` goes out as JSON `null`. `ChannelCredentials.uid` is an `int`,
so the host resolves it or stops. Keep the first two tiers. The last two make up
a uid no token was minted for.

**The RTM fields are optional.** `create_session` fetches the feed with
`camera.async_get_live_feed(refresh=True)` (`whep_proxy.py:131`), which checks
only `channel_id` and `rtc_token` (`camera.py:606-607`). A feed without RTM fields
still joins; `start_live` returns `False` and the POST continues with a warning
(`agora_rtm.py:61-64`, `whep_proxy.py:175-180`). Build the two credentials
separately so that stays true:

```python
from pyagorartc import ChannelCredentials, RtmCredentials

from .const import AGORA_APP_ID


def petkit_channel_credentials(feed: LiveFeed, account_user_id: str | None) -> ChannelCredentials:
    uid = feed.uid
    if uid is None and account_user_id and account_user_id.isdigit():
        uid = int(account_user_id)
    if uid is None or not feed.channel_id or not feed.rtc_token:
        raise ValueError("live feed has no usable Agora uid, channel or token")
    return ChannelCredentials(app_id=AGORA_APP_ID, channel_name=feed.channel_id, token=feed.rtc_token, uid=uid)


def petkit_rtm_credentials(feed: LiveFeed) -> RtmCredentials | None:
    """``None`` when a field is missing, as ``AgoraRTMSignaling._extract_rtm_credentials`` did."""
    user_id = (feed.app_rtm_user_id or "").strip()
    peer_user_id = (feed.dev_rtm_user_id or "").strip()
    token = (feed.rtm_token or "").strip()
    if not (user_id and peer_user_id and token):
        return None
    return RtmCredentials(app_id=AGORA_APP_ID, user_id=user_id, peer_user_id=peer_user_id, token=token)
```

The account user id has no public accessor. `PetKitClient._session` is a
`SessionInfo` whose `user_id` is a `str` (`client.py:174`, `containers.py:63`), so
the camera adds:

```python
@property
def account_user_id(self) -> str | None:
    session = getattr(self.coordinator.config_entry.runtime_data.client, "_session", None)
    return session.user_id if session is not None else None
```

It reads a private attribute, as `_resolve_agora_user_id` did. `ValueError` is
already in the WHEP view's catch (502, `whep_proxy.py:582`). `LiveFeed` has no
encryption fields, so `encryption` stays `None`.

### 3.3 Edge discovery and ICE servers (`camera.py`)

`_refresh_agora_context` takes the credentials and returns the response:

```python
async def _refresh_agora_context(self, creds: ChannelCredentials) -> APResponse:
    self._agora_response = None
    client = AgoraAPClient(async_get_clientsession(self.hass))  # borrowed: plain call, no async with (§2.3)
    self._agora_response = await client.choose_server(creds)
    self._ice_servers = to_rtc_ice_servers(self._agora_response)
    return self._agora_response
```

- Both callers change: `async_prepare_agora` (`camera.py:222-227`) builds the
  credentials from its feed, and `async_refresh_agora_context`
  (`camera.py:614-619`) takes and returns what the upstream manager needs.
  `async_setup_entry` already collects the prefetch's exceptions
  (`camera.py:126-135`), `ValueError` and `APError` included.
- `choose_server` raises instead of leaving `None`, so the
  `if agora_response is None` branch (`whep_proxy.py:136-137`) goes.
- The AP call was `ssl=False` in PetKit's copy (`agora_api.py:458`); it is
  verified now (D10). The gateway socket already was (`agora_websocket.py:28-36`,
  `:166`). `AgoraAPClient(..., verify_ssl=False)` is the escape hatch.
- PetKit's TURN credentials were AP detail `8`/`4` first, then the uid-derived
  pair (`agora_api.py:107-116`). The library default is the uid pair (D12). Pass
  `strategy=TurnCredentialStrategy.DETAIL_FIRST` in `to_rtc_ice_servers` to keep
  PetKit's order (Q5). These servers reach only HA's frontend list
  (`camera.py:204-207`): the go2rtc → Agora leg uses go2rtc's own ICE config.
- PetKit's AP request always carried detail `6` = `str(uid)` (`agora_api.py:320-321`,
  `:375`). The library sends it only when `ChannelCredentials.string_uid` is set,
  and then also puts `string_uid` into the join, which PetKit never sent
  (`agora_websocket.py:686`). Leave `string_uid` unset (Q9, backlog).

### 3.4 The session (`whep_proxy.py`)

`PetkitAgoraUpstreamManager` keeps one upstream per device and already closes
the previous one first (`whep_proxy.py:129`). Every option PetKit passes
(`whep_proxy.py:154-162`) has a `SessionOptions` field; two more keep old
behaviour the library turned off by default:

| `AgoraWebSocketHandler` argument | `SessionOptions` |
|---|---|
| `prefer_instant_video=True` | `instant_video=True` |
| `subscribe_retry_delay=1.0` | `subscribe_retry_delay_s=1.0` |
| `subscribe_retry_attempts=3` | `subscribe_retry_attempts=3` |
| `declare_remote_video_ssrc=True` | `declare_remote_video_ssrc=True` |
| `disable_audio_answer=True` | `disable_audio=True` |
| hard-coded `codec: "h264"` (`agora_websocket.py:679`, `:553`) | `client_codec="h264"` |
| unconditional `set_client_role(host, 0)` (`agora_websocket.py:354`) | `send_set_client_role=True` (D6, Q3) |
| `p2p_lost` disconnects (`agora_websocket.py:414-422`) | `end_on_p2p_lost=True` (D22, Q13) |
| `rtc_token_provider=` | the `token_provider=` argument |
| `on_connection_lost=` | the `on_closed=` argument |

```python
from pyagorartc import AgoraSession, CloseReason, PyAgoraRTCError, SessionOptions

PETKIT_OPTIONS = SessionOptions(
    client_codec="h264",
    instant_video=True,
    subscribe_retry_attempts=3,
    subscribe_retry_delay_s=1.0,
    declare_remote_video_ssrc=True,   # go2rtc/pion needs a declared remote SSRC
    disable_audio=True,
    send_set_client_role=True,        # until Q3 is answered on real hardware
    end_on_p2p_lost=True,             # PetKit ended the session on p2p_lost (D22)
)


async def create_session(self, camera: PetkitWebRTCCamera, offer_sdp: str) -> tuple[str, str]:
    device_id = str(camera.device.id)
    await self.close_session(device_id)  # sessions are single-use; the old heartbeat must stop
    live_feed = await camera.async_get_live_feed(refresh=True)
    if live_feed is None:
        raise RuntimeError("Live feed unavailable or missing RTM credentials")
    creds = petkit_channel_credentials(live_feed, camera.account_user_id)
    ap = await camera.async_refresh_agora_context(creds)
    agora_rtm = AgoraRTMSignaling(async_get_clientsession(self.hass))
    session_id = secrets.token_hex(16)

    async def refresh_rtc_token() -> str | None:
        feed = await camera.async_get_live_feed(refresh=True)
        if feed is None or not feed.rtc_token:
            return None
        await agora_rtm.update_tokens(feed)
        return feed.rtc_token

    async def on_closed(reason: CloseReason) -> None:
        if reason in (CloseReason.CLOSED_BY_HOST, CloseReason.JOIN_FAILED):
            return
        await self.close_session(device_id, session_id=session_id)

    session = AgoraSession(
        creds,
        ap,
        options=PETKIT_OPTIONS,
        token_provider=refresh_rtc_token,
        on_closed=on_closed,
        spawn=lambda coro: self.hass.async_create_background_task(coro, f"petkit agora {device_id}"),
    )
    if not await agora_rtm.start_live(live_feed):
        LOGGER.warning("go2rtc upstream start_live/heartbeat not active for %s", device_id)
    try:
        answer_sdp = await session.join(offer_sdp, session_id)
    except BaseException:  # cancellation too: the heartbeat is already running
        await asyncio.gather(session.close(), agora_rtm.stop_live(send_stop=True), return_exceptions=True)
        raise
    upstream = AgoraUpstreamSession(
        session_id=session_id,
        camera=camera,
        agora_session=session,
        agora_rtm=agora_rtm,
        location_path=f"/api/petkit/whep_upstream/{device_id}/{session_id}",
    )
    upstream.refresh_task = self.hass.async_create_background_task(
        self._refresh_tokens(upstream), f"petkit go2rtc upstream refresh {device_id}"
    )
    async with self._lock:
        self._sessions[device_id] = upstream
    return session_id, answer_sdp


async def close_session(self, device_id: str, *, session_id: str | None = None) -> bool:
    async with self._lock:
        upstream = self._sessions.get(device_id)
        if upstream is None or (session_id is not None and upstream.session_id != session_id):
            return False
        del self._sessions[device_id]
    ...  # cancel refresh_task as today (whep_proxy.py:234-237)
    await asyncio.gather(
        upstream.agora_session.close(), upstream.agora_rtm.stop_live(send_stop=True), return_exceptions=True
    )
    return True
```

Why each piece is there:

| Piece | Reason |
|---|---|
| Check order: feed → credentials → AP → RTM `start_live` → join | PetKit's order (`whep_proxy.py:131-190`). `start_live` goes before the join so the camera is publishing when the gateway answers. |
| `close_session(device_id)` first | A session is single-use (architecture §2). `create_session` is not serialised per device, so two concurrent POSTs can each close and then both store. A per-device lock around `create_session` removes that. |
| `session_id` made before the session | `on_closed` must close *this* upstream, not whatever holds the device slot by then. A bare `close_session(device_id)`, as `_on_connection_lost` did (`whep_proxy.py:148-152`), can close a replacement stored in the meantime. |
| `on_closed` returns on `CLOSED_BY_HOST` | The host is already in `close_session` (§1.5). |
| `on_closed` returns on `JOIN_FAILED` | The session fires it before `join()` raises (D23); the `except` above already cleans up and the view answers 502. |
| Every other reason → `close_session` | `_on_connection_lost` ran the same teardown: cancel the refresh task, disconnect, `stop_live(send_stop=True)`. It fired on a `WebSocketException` in the loop and on `p2p_lost` (`agora_websocket.py:292-294`, `:414-422`); a clean socket close and a gateway `quit` did nothing. Now `SOCKET_CLOSED` and `GATEWAY_QUIT` tear down too. Calling `close()` from inside `on_closed` is safe (D13). |
| `refresh_rtc_token` as `token_provider` | PetKit's closure (`whep_proxy.py:141-146`), unchanged. The session calls it at most once per 30 s (D8). |
| The inline-candidate loop is gone | `join()` extracts the offer's `a=candidate:` lines itself (D11). |
| `if not answer_sdp` is gone | `join()` answers or raises (D9). |
| `on_peer_left` unset | PetKit had no `on_user_offline` handler; the library unsubscribes a departed publisher itself. Re-sending `start_live` from it is the natural recovery, untested. |

- **Views.** The upstream POST view catches `(OSError, RuntimeError, ValueError)`
  (`whep_proxy.py:582`). Add `PyAgoraRTCError`, or AP and join failures become
  HTTP 500 instead of 502.
- **PATCH: accept and ignore.** Candidates from a PATCH never reached Agora in
  PetKit's copy either: they were appended to `handler.candidates`
  (`whep_proxy.py:270-271`), which only `connect_and_join` reads
  (`agora_websocket.py:136`). The library has no post-join trickle either (D11,
  Q4). `add_session_candidates` keeps its session-id check (404 on a mismatch)
  and its 204. It may count `parse_trickle_fragment(body)` for the DEBUG line,
  but must not call `add_ice_candidate`. Do not switch to 405: that is what WHEP
  specifies for a server without trickle, but it changes what go2rtc sees and
  gains nothing.
- **DELETE → `close_session(device_id, session_id=session_id)`.** The view
  ignores its `session_id` today (`whep_proxy.py:632-634`), so a late DELETE for
  a replaced upstream closes the new one.
- `AgoraUpstreamSession.agora_handler` becomes `agora_session: AgoraSession`.
  `get_session_rtm` is unchanged.

### 3.5 RTM control (host vocabulary on the library transport)

`RtmRestClient(creds, session=None, *, hosts=RTM_HOSTS)` sends one peer message
and returns the ack code. It raises `RtmError` when every host fails, the server
refuses with another status, or the ack is outside `accepted_codes`. Endpoint
rotation (404, 429, 5xx and connection errors move on; the last good host goes
first), the three auth headers, the compact JSON payload and one send at a time
are the library's. Those are PetKit's `_send_command` rules
(`agora_rtm.py:190-333`), with one difference: the library holds its send lock
across the whole rotation, while PetKit's held it per host (`agora_rtm.py:242-249`).

PetKit's controller keeps its commands, retries and cadence. `start_live(feed)`
keeps its signature because both callers pass a feed: the upstream manager
(`whep_proxy.py:175`) and the camera's manual start (`camera.py:436`). The client
is therefore built in `start_live`, not in `__init__`:

```python
HEARTBEAT_INTERVAL_SECONDS = 0.5
HEARTBEAT_MAX_FAILURES = 10
START_LIVE_RETRIES = 5
START_LIVE_RETRY_DELAY_SECONDS = 1.0
STOP_SUCCESS_CODES = frozenset({"message_sent", "message_delivered", "message_offline"})


class AgoraRTMSignaling:
    def __init__(self, http: aiohttp.ClientSession, is_sd: int = 0) -> None:
        self._http = http  # HA's session: borrowed, never closed
        self._is_sd = is_sd
        self._rtm: RtmRestClient | None = None
        self._peer: tuple[str, str] | None = None
        self._state_lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start_live(self, live_feed: LiveFeed) -> bool:
        if (creds := petkit_rtm_credentials(live_feed)) is None:
            return False
        async with self._state_lock:
            if self._rtm is None or self._peer != (creds.user_id, creds.peer_user_id):
                await self._teardown_locked(send_stop=False)
                self._rtm, self._peer = RtmRestClient(creds, self._http), (creds.user_id, creds.peer_user_id)
            else:
                self._rtm.update_token(creds.token)
            if not await self._send_start_live_with_retry():
                return False
            if self._heartbeat_task is None or self._heartbeat_task.done():
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            return True

    async def _send_start_live_with_retry(self) -> bool:
        for attempt in range(START_LIVE_RETRIES):
            try:
                await self._rtm.send_peer_message({"cmd": "start_live", "payload": {"isSD": self._is_sd}})
            except RtmError as err:
                LOGGER.debug("start_live attempt %d/%d not acknowledged: %s", attempt + 1, START_LIVE_RETRIES, err)
            else:
                return True
            if attempt < START_LIVE_RETRIES - 1:
                await asyncio.sleep(START_LIVE_RETRY_DELAY_SECONDS)
        return False

    async def _heartbeat_loop(self) -> None:
        failures = 0
        while failures < HEARTBEAT_MAX_FAILURES:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                await self._rtm.send_peer_message(
                    {"cmd": "live_heartbeat", "payload": {"isSD": self._is_sd}}, wait_for_ack=False
                )
            except RtmError:
                failures += 1
            else:
                failures = 0
        LOGGER.warning("Signaling heartbeat failed %d consecutive times; stopping heartbeat", failures)
```

| Command | PetKit today | Call |
|---|---|---|
| `start_live` | 5 attempts, 1.0 s apart (no sleep after the last), `wait_for_ack=true`, `message_sent`/`message_delivered` (`agora_rtm.py:159-188`) | `send_peer_message({"cmd": "start_live", "payload": {"isSD": 0}})` |
| `live_heartbeat` | sleep 0.5 s, then send, `wait_for_ack` off; stops after 10 consecutive failures and ends nothing else (`agora_rtm.py:341-368`) | `send_peer_message({"cmd": "live_heartbeat", "payload": {"isSD": 0}}, wait_for_ack=False)` |
| `stop_live` | heartbeat cancelled and awaited first, then sent only with `send_stop=True`, no payload, `wait_for_ack` off, failures swallowed (`agora_rtm.py:370-385`) | `send_peer_message({"cmd": "stop_live"}, wait_for_ack=False, accepted_codes=STOP_SUCCESS_CODES)` inside `with suppress(RtmError)` |
| `ptz_ctrl` | `payload={"type": t, "ptz_dir": d}`, `wait_for_ack=true`, returns `bool` (`agora_rtm.py:87-99`) | `send_peer_message({"cmd": "ptz_ctrl", "payload": {"type": t, "ptz_dir": d}})`; `RtmError` → `False` |

- **Heartbeat task.** It stays a host task, created with `asyncio.create_task` as
  today (`agora_rtm.py:339`) and cancelled and awaited in `_teardown_locked`. It
  cannot be the session's `keepalive`: it starts with `start_live`, before the
  join, and the manual start in `camera.py:419-445` has no session at all.
- **Teardown.** `_teardown_locked` no longer closes an HTTP session
  (`agora_rtm.py:387-389`): the one it uses is HA's. It drops `self._rtm`.
- **Token refresh.** `update_tokens(feed)` becomes
  `self._rtm.update_token(creds.token)` under the state lock, and only when
  `app_rtm_user_id` and `dev_rtm_user_id` are unchanged. A feed naming other
  users is ignored, as today (`agora_rtm.py:101-113`); the next `start_live`
  builds a new client, since `RtmCredentials` is frozen. Two paths call it:
  - the 20-minute `_refresh_tokens` loop (`whep_proxy.py:33`, `:289-296`), which
    fetches through `_get_live_feed_for_webrtc`. That helper can send
    `temporary_open_camera` to a `TEMP_CAMERA_TYPES` device whose feed lacks RTM
    fields (`webrtc_common.py:125-152`).
  - `refresh_rtc_token` on every gateway renewal (§3.4).
- **Two controllers.** `camera.py:161` builds its own for the manual start/stop
  and for PTZ when no upstream exists (`camera.py:576-583`). It needs the HA
  session in its constructor too; the app id now comes with `RtmCredentials`.
- **Unverified ack codes.** No RTM ack has been captured; `message_offline`
  comes from PetKit's `STOP_SUCCESS_CODES` (`agora_rtm.py:32-36`, Q14).

### 3.6 Behaviour changes PetKit will see

| Change | PetKit today | Decision | What the host does / watches |
|---|---|---|---|
| MID header extension stripped from the answer | every gateway extension the offer also has is echoed, MID included (`agora_websocket.py:950-963`) | D16, Q7 | **The change most likely to alter behaviour.** pion then routes RTP by SSRC alone: fine while the answer declares the stream's SSRC. When no stream is announced within 15 s the answer has no `a=ssrc` and, now, no MID either. If go2rtc logs unhandled SSRCs or shows no video, pass `strip_mid_extension=False` (PetKit's bytes) and report it under Q7. |
| Candidates in the join ORTC are the offer's inline ones, unfiltered | the offer's candidates, filtered to srflx/prflx/Agora-TURN relays (`whep_proxy.py:163-173`, `camera.py:654-671`) | D11 | go2rtc's host candidates now reach the gateway. The gateway is ice-lite, so they should be inert. To restore the filter, strip the `a=candidate:` lines from the offer and `add_ice_candidate` each of `filter_candidates(extract_inline_candidates(offer), turn_ips)` before `join` (backlog). |
| A stream announced after the join is subscribed only once `on_user_online` for its publisher is seen | subscribe on `on_add_video_stream` alone; `on_user_online` only recorded a uid nothing read (`agora_websocket.py:433-470`) | Q18 | Watch for `remote_streams` non-empty while `remote_users` lacks that uid and `on_stream` never fires. Streams in the join payload are unaffected. |
| Subscribe retries only while unacknowledged | 1 s after the answer, re-sent every known stream 3 times regardless of ack (`agora_websocket.py:616-646`) | D1 | Nothing expected; a gateway that never acks gets the same 4 sends. |
| Answer `a=setup` mirrors the gateway's DTLS role | always `active` (`agora_websocket.py:945`) | D5 | Identical when the gateway says `client` or `auto`. If a PetKit gateway ever answers `server`, the answer now says `passive` and go2rtc must be the DTLS client; a DTLS stall there is this. |
| ORTC DTLS role `server` | `client` (`agora_sdp.py:167`) | D4 | `ortc_dtls_role="client"` restores the old bytes if DTLS stalls (Q2). |
| `a=rtcp-fb` reaches Agora in the client ORTC | never: PetKit's parser has no `rtcp-fb` case, so every `rtcpFeedbacks` was empty (`agora_sdp.py:72-123`, `:196-207`) | D3 | The gateway may now return nack/pli/transport-cc and the answer carries them (PetKit's answer builder already wrote whatever came back, `agora_websocket.py:984-993`). Expect better loss recovery; watch that the camera's stream does not change. |
| H265 and other receive-only codecs leave the send caps (`can_send`) | every offered codec in `send` and `recv` by direction (`agora_sdp.py:228-241`) | D1 | Nothing: go2rtc's offer is receive-only. |
| `leave` sent on close | the socket was closed without one (`agora_websocket.py:1179-1216`) | D1 | Nothing; the gateway frees the slot sooner. |
| `enablePreallocPC: true`, `sdk_version` 4.24.3 | `false`, 4.24.0 (`agora_websocket.py:703`, `:671`) | D7 | `prealloc_pc=False` restores it (Q10). |
| A token-provider `None` resends the last token | `None` or an exception skipped the renew (`agora_websocket.py:318-328`) | D8 | Nothing expected; the gateway accepted a repeated token on Mammotion. |
| Renewal calls `token_provider` at most once per 30 s | every `will_expire` notice, about once a second (`agora_websocket.py:283-285`) | D8 | Each call was a PetKit API fetch and an RTM token update; expect far fewer (Q11). |
| `on_closed` replaces `on_connection_lost`, is async, fires once per ending | sync callback on a loop `WebSocketException` or `p2p_lost` only | D14, D23 | §3.4. A clean socket close and a gateway `quit` now tear the upstream down too. |
| A failed join raises; no fabricated SDP | `None` on failure (`agora_websocket.py:214-216`); no fabricated SDP either | D9 | Catch `PyAgoraRTCError` in the view (§3.4). |
| AP call TLS-verified | `ssl=False` (`agora_api.py:458`); the gateway socket was already verified | D10 | Nothing, unless behind an intercepting proxy. |
| AP detail `6` not sent | always `str(uid)` (`agora_api.py:320-321`) | Q9 | Nothing expected (§3.3). |
| A failed AP service block is skipped, not fatal | | D1 | Nothing. |
| RTM failures raise `RtmError` | `_send_command` returned `False` | D18 | Wrap as in §3.5. |
| Unchanged: `set_client_role` (with the option), `p2p_lost` ends the session (with the option), nested `userAttributes`, `enableInstantVideo`, the deferred answer with a declared SSRC and its 15 s fallback, subscribe dedupe by `(uid, ssrc)`, the join-payload stream walk, fingerprint merge only when missing (D26), 3 s ping | | | |

### 3.7 Manifest and requirements

Today (`manifest.json`):

```json
"loggers": ["petkit"],
"requirements": [
  "pypetkitapi==1.29.0",
  "aiofiles==24.1.0",
  "paho-mqtt==2.1.0",
  "websockets==15.0.1",
  "sdp-transform==1.1.0"
]
```

After:

```json
"loggers": ["petkit", "pyagorartc"],
"requirements": [
  "pypetkitapi==1.29.0",
  "aiofiles==24.1.0",
  "paho-mqtt==2.1.0",
  "pyagorartc==0.2.0"
]
```

- **Drop `websockets==15.0.1` and `sdp-transform==1.1.0`.** After the migration
  nothing in PetKit imports either: `websockets` only in `agora_websocket.py`,
  `sdp_transform` only in `agora_websocket.py` and `whep_proxy.py:14`.
  pyagorartc declares `websockets>=13.1` and `sdp-transform>=1.1.0` itself (§1.6).
  The exact `websockets` pin is the one that fights HA core. Core's constraint is
  a floor, `websockets>=15.0.1`, and HA 2026.9.3 installs 16.0. The library's
  floor of 13.1 sits below it and never conflicts.
- **Keep `aiofiles` and `paho-mqtt`.** They are not Agora's
  (`camera.py:287`, `:309`; `iot_mqtt.py`).
- **Keep importing `webrtc_models`.** It is HA's API type (`camera.py:28`) and
  core pins it; pyagorartc does not need it (D19).
- **Add `pyagorartc` to `loggers`.** HA's debug toggle then captures the library,
  which the Q11, Q14 and Q18 runs need (§4).

### 3.8 Checklist

- [ ] `manifest.json`: drop `websockets` and `sdp-transform`, add
      `pyagorartc==0.2.0` and the `pyagorartc` logger (§3.7).
- [ ] Add `petkit_channel_credentials()` and `petkit_rtm_credentials()` (§3.2),
      and the camera's `account_user_id` property.
- [ ] `camera.py`: `_refresh_agora_context(creds)` on `AgoraAPClient`, returning
      the `APResponse`; `to_rtc_ice_servers` (with `DETAIL_FIRST` to keep the TURN
      order); both callers adapted (§3.3). Delete `_filter_candidates` and
      `filter_agora_candidates`.
- [ ] `whep_proxy.py`: rewrite `create_session` with `PETKIT_OPTIONS` (§3.4);
      `close_session(device_id, *, session_id=None)`; `AgoraUpstreamSession`
      holds `agora_session`; add `PyAgoraRTCError` to the POST view's catch; the
      DELETE view passes its `session_id`.
- [ ] `whep_proxy.py`: `add_session_candidates` validates the session and
      returns 204 without forwarding; delete `_parse_trickle_candidates` and the
      `sdp_transform` import.
- [ ] `agora_rtm.py`: keep `AgoraRTMSignaling` and its constants; replace
      `_send_command`, `_iter_endpoints`, `_ensure_session` and the endpoint
      tables with `RtmRestClient` built in `start_live` on HA's session (§3.5).
      Both constructors pass the session.
- [ ] `webrtc_common.py`: delete `_add_offer_candidates` and
      `_resolve_agora_user_id`.
- [ ] Delete `agora_api.py`, `agora_sdp.py`, `agora_websocket.py`.
- [ ] Before release, run one session through go2rtc on each camera model with
      `PETKIT_OPTIONS` and `pyagorartc` at DEBUG. Confirm video, then the Q18
      check, then the Q3 and Q13 runs (§4).

## 4. Open questions each host can close on real hardware

| Q | Mammotion | PetKit | How |
|---|---|---|---|
| Q2 DTLS role in the ORTC | yes | yes | One session with `SessionOptions(ortc_dtls_role=None)`. DTLS completes and video plays → the SDK's "send none" is safe. PetKit shipped `client` (`agora_sdp.py:167`); if the default `server` stalls there, `ortc_dtls_role="client"` first. |
| Q3 `set_client_role` after join | — (mowers leave when it is sent, D6) | yes | `PETKIT_OPTIONS` with `send_set_client_role=False`. Video still flows after 10 s and the camera stays in `remote_users` → drop the flag. The camera leaving within a second of first video, as the mowers did, means keep it. |
| Q5 AP detail `8`/`4` TURN credentials | yes | weak (its TURN servers reach only HA's frontend list, §3.3) | `get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)` in `to_rtc_ice_servers`, with a relay-only browser (`iceTransportPolicy: "relay"`). A TURN 401 in the browser's ICE log answers it. |
| Q6 `openEncrypt` | yes | — (`LiveFeed` has no such field) | Nothing to add on Mammotion: the WARNING already fires per offer when `openEncrypt` is set (§2.5). Watch the log; a hit is the answer, and a capture of that session is wanted. |
| Q7 MID extension | — | yes | `PETKIT_OPTIONS` as shipped strips it. If go2rtc shows no video or logs undeclared SSRCs, retry with `strip_mid_extension=False`. Either way, note whether the answer carried `a=ssrc` (a stream was announced within 15 s). |
| Q10 `enablePreallocPC` / `enableInstantVideo` | yes | yes | Mammotion: `instant_video=True`. PetKit already sends it; flip `prealloc_pc=False` (PetKit's old value, `agora_websocket.py:703`). One knob per run; compare time to first frame. |
| Q11 renew debounce | yes | yes | Enable debug logging for `pyagorartc` (in `loggers` on both hosts, §2.6, §3.7), leave a session running past token expiry, and count `renew_token` sends and any gateway error after repeats. `SessionOptions(renew_debounce_s=...)` changes the 30 s window. On PetKit each renewal is also a live-feed fetch. |
| Q13 `on_p2p_lost` for a subscriber | — (ignored, D22) | yes | `PETKIT_OPTIONS` ends the session on it. Log the frame's `code`/`error` (the library's WARNING carries both) whenever it fires, noting whether video had actually stopped. Frames during a healthy stream → try `end_on_p2p_lost=False`. |
| Q14 RTM ack shapes | — | yes | `pyagorartc.rtm` at DEBUG logs `status`, `result` and `code` for every `start_live`, `live_heartbeat`, `stop_live` and `ptz_ctrl`. One of each, plus a `stop_live` to an idle camera (expected `message_offline`), closes it. |
| Q18 subscribe gate | — (the gate is HA-Luba's rule, protocol §3.2) | yes | After the answer, log `session.remote_users`, `session.remote_streams` and whether `on_stream` fired. A stream in `remote_streams` whose uid is not in `remote_users`, with no `on_stream`, is the gate holding a subscribe: the PetKit gateway does not send `on_user_online` for it. Then the backlog's `subscribe_requires_online=False` is needed. |

Report each result as a new decision in `decisions.md` that closes the
question.
