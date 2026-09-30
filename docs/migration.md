# Migration

How each known host stops carrying its own `agora_*` modules and depends on
`pyagorartc` instead. Constitution §10 requires this file to be current before
any release that changes the public surface.

The hosts today:

| Host | Local Agora code | Entry points |
|---|---|---|
| Mammotion (`HA-Luba/custom_components/mammotion/`) | `agora_api.py`, `agora_sdp.py`, `agora_websocket.py` | `camera.py`, `coordinator.py::async_check_stream_expiry` |
| PetKit (`homeassistant_petkit/custom_components/petkit/`) | `agora_api.py`, `agora_sdp.py`, `agora_websocket.py`, `agora_rtm.py`, parts of `webrtc_common.py` and `whep_proxy.py` | `whep_proxy.py::PetkitAgoraUpstreamManager`, `camera.py` |

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
udp, tcp, turns for the first TURN edge). The TURN credential is the
uid-derived one by default (D12). `strategy=TurnCredentialStrategy.
DETAIL_FIRST` restores PetKit's old order (Q5).

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
| `extract_inline_candidates(offer_sdp) -> list[IceCandidate]` | PetKit's inline `a=candidate:` loop in `whep_proxy.py` and the unused `webrtc_common._add_offer_candidates`; `join()` already sends these, so a host calls it only to inspect an offer |
| `filter_candidates(candidates, turn_ips) -> list[IceCandidate]` | PetKit `camera.PetkitWebRTCCamera._filter_candidates` |
| `parse_trickle_fragment(fragment) -> list[IceCandidate]` | PetKit `whep_proxy._parse_trickle_candidates` |

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
down from `on_closed` should return early on `CLOSED_BY_HOST`: it is already
in its own close path.

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
| `coordinator.py::async_check_stream_expiry` (token cache, all-camera fallback, 50504) | stays; its AP block shrinks (§2.3) |
| `RTCIceServer` conversion | stays in the host (§1.2) |
| `tests_ha/test_agora_*.py`, `test_fpv_keepalive.py` | ported to the library's test tree (backlog); delete from HA-Luba with the modules |

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
  An encrypted channel will not show a picture. Log a warning when `openEncrypt` is non-zero, or
  refuse the offer with a clear `WebRTCError`. Q6 asks whether any mower
  sets it.
- **Area code.** The captured `areaCode` is `AREA_CODE_EU`, an Android SDK
  enum name. Whether the Web AP accepts that form is unverified (Q12).
  Keep the default until a capture shows the mapping.

### 2.3 Edge discovery and ICE servers (`coordinator.py`)

Replace the `AgoraAPIClient` block in `async_check_stream_expiry`:

```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyagorartc import AgoraAPClient, PyAgoraRTCError

if stream_data is not None and stream_data.data is not None:
    try:
        creds = mammotion_credentials(stream_data.data)
        async with AgoraAPClient(async_get_clientsession(self.hass)) as ap_client:
            ap = await ap_client.choose_server(creds)
    except PyAgoraRTCError:
        LOGGER.exception("Agora edge discovery failed")
        self.ice_servers = []
    else:
        self.ice_servers = to_rtc_ice_servers(ap)
        self._agora_response = ap
```

The broad `except Exception` narrows to `PyAgoraRTCError`. The AP call is now
TLS-verified (D10); `AgoraAPClient(..., verify_ssl=False)` exists for
proxied networks.

### 2.4 The session (`camera.py`)

The entity holds at most one session, created per offer.

```python
import time

from pyagorartc import AgoraSession, APResponse, CloseReason, IceCandidate, PyAgoraRTCError, SessionOptions


class MammotionWebRTCCamera(MammotionCameraBaseEntity):
    _session: AgoraSession | None = None
    _early_candidates: list[IceCandidate]  # initialised to [] in __init__

    def _new_session(self, data: StreamSubscriptionResponse, ap: APResponse) -> AgoraSession:
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
            keepalive_interval_s=3.0,
            deadline=deadline,
            spawn=lambda coro: self.hass.async_create_background_task(
                coro, f"mammotion agora {self.entity_id}"
            ),
        )

    async def _async_answer_offer(self, offer_sdp, session_id, send_message) -> bool:
        stream_data, ap = await self.coordinator.async_check_stream_expiry(force=True)
        if stream_data is None or ap is None:
            send_message(WebRTCError("500", "No stream data available for WebRTC offer"))
            return False
        # ... target_uid / all_cameras_streaming check unchanged ...
        await self.coordinator.async_send_command("send_todev_ble_sync", sync_type=3)
        session = self._new_session(stream_data, ap)
        for candidate in self._early_candidates:
            session.add_ice_candidate(candidate)
        self._early_candidates = []
        self._session = session
        try:
            answer = await session.join(offer_sdp, session_id)
        except PyAgoraRTCError as err:
            _LOGGER.warning("Agora join failed: %s", err)
            send_message(WebRTCError("500", f"WebRTC negotiation failed: {err}"))
            return False  # the caller's finally closes the session
        send_message(WebRTCAnswer(answer))
        return True

    async def async_on_webrtc_candidate(self, session_id, candidate) -> None:
        if self._session is None:
            self._early_candidates.append(to_ice_candidate(candidate))
        else:
            self._session.add_ice_candidate(to_ice_candidate(candidate))

    async def async_close_webrtc_session(self) -> None:
        if (session := self._session) is not None:
            self._session = None
            await session.close()
        await self.coordinator.async_release_camera_session(self.entity_description.key)

    async def _on_peer_left(self, uid: int) -> None:
        await self._recover_stream()  # BLE sync + get_stream_subscription, unchanged

    async def _on_closed(self, reason: CloseReason) -> None:
        if reason is CloseReason.CLOSED_BY_HOST:
            return
        message = {
            CloseReason.GATEWAY_QUIT: "Another camera on this mower took over the stream",
            CloseReason.DEADLINE: "4G streaming budget exhausted",
        }.get(reason, "Stream lost")
        viewers = list(self._sessions.values())
        self._sessions.clear()
        for send_message in viewers:
            send_message(WebRTCError("503", message))
        await self.async_close_webrtc_session()
```

`async_teardown_stream` replaces `self._agora_handler.disconnect()` with the
same `session.close()`. `_fpv_keepalive` and `_recover_stream` are
unchanged. `_async_session_ended` becomes `_on_closed`. The
`websockets.exceptions.WebSocketException` / `json.JSONDecodeError` catch in
`_async_answer_offer` and the `(OSError, ValueError, TypeError)` catch in
`_perform_webrtc_negotiation` go: the library raises `PyAgoraRTCError`
subclasses (§1.4). `_perform_webrtc_negotiation` can be inlined.

`token_provider` stays unset: the gateway accepts the join token on renew
(D8), and minting a new Mammotion stream token restarts the mower's stream.

The `deadline` is computed only on 4G. Today's loop enforces
`availableTime` only while `keepalive` returns `True`, which it never does
on WiFi. The library's deadline is independent of the keep-alive (D15), so
gating it on `is_on_4g` keeps WiFi streams unbounded as they are now.

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

### 2.6 Manifest and requirements

`custom_components/mammotion/manifest.json`:

```json
"requirements": [
  "pymammotion==0.10.1",
  "pyagorartc==x.y.z"
]
```

HA-Luba pins neither `sdp-transform` nor `websockets` today; both arrive
through `pymammotion`, which declares `sdp-transform>=1.1.0`,
`websockets>=13.1` and `webrtc-models>=0.3.0` and imports none of them.
Once HA-Luba no longer ships `agora_*.py`, drop those three from
`pymammotion`'s `pyproject.toml` in its next release. Until then they are
harmless duplicates.

### 2.7 Checklist

- [ ] Add `pyagorartc==x.y.z` to `manifest.json`.
- [ ] Add `mammotion_credentials()` (§2.2) next to the coordinator.
- [ ] `coordinator.py`: replace the `AgoraAPIClient` block (§2.3); import
      `APResponse` for the `async_check_stream_expiry` return type.
- [ ] `camera.py`: drop `AgoraWebSocketHandler` from `__init__`; add
      `_session`, `_early_candidates`, `_new_session`, `_on_peer_left`,
      `_on_closed` (§2.4).
- [ ] `camera.py`: replace every `self._agora_handler.disconnect()` with
      `session.close()`; drop the `websockets` and `json` imports used only
      for the old catch.
- [ ] Decide what to do on `openEncrypt != 0` (warn or refuse) (D20).
- [ ] Delete `agora_api.py`, `agora_sdp.py`, `agora_websocket.py`.
- [ ] Delete `tests_ha/test_agora_*.py` and `test_fpv_keepalive.py` once
      the library tests cover them; keep host tests for `_on_closed` and
      `_new_session`'s deadline gating.
- [ ] Run one Wi-Fi and one 4G session on a Luba 2 and a Yuka (all three
      `target_uid`s) before release.
- [ ] Open a `pymammotion` change dropping its unused `sdp-transform`,
      `websockets` and `webrtc-models` requirements.

## 3. PetKit

### 3.1 What moves where

| Today (`custom_components/petkit/`) | Becomes |
|---|---|
| `agora_api.AgoraAPIClient`, `AgoraResponse`, `SERVICE_IDS` | `pyagorartc.AgoraAPClient`, `pyagorartc.APResponse`, defaults |
| `agora_sdp.py` | internal to `AgoraSession` |
| `agora_websocket.AgoraWebSocketHandler(rtc_token_provider=, prefer_instant_video=, subscribe_retry_delay=, subscribe_retry_attempts=, declare_remote_video_ssrc=, disable_audio_answer=, on_connection_lost=)` | `pyagorartc.AgoraSession(creds, ap, options=SessionOptions(...), token_provider=, on_closed=, spawn=)` |
| `handler.connect_and_join(live_feed=, offer_sdp=, session_id=, app_id=, agora_response=)` | `await session.join(offer_sdp, session_id)`; `app_id` moves into the credentials |
| `handler.add_ice_candidate(RTCIceCandidateInit)` / `handler.candidates = ...` | `session.add_ice_candidate(IceCandidate)` |
| `handler.disconnect()` | `await session.close()` |
| `whep_proxy.py` inline `a=candidate:` loop, `webrtc_common._add_offer_candidates` | nothing: `join()` sends the offer's inline candidates itself |
| `camera._filter_candidates`, `filter_agora_candidates` | `filter_candidates(candidates, turn_ips)` |
| `whep_proxy._parse_trickle_candidates` | `parse_trickle_fragment(fragment)` |
| `agora_websocket` fingerprint injection (`get_gateway_addresses() or addresses`) | inside the session, only when the gateway sends none (D26) |
| `agora_rtm.AgoraRTMSignaling._send_command`, `_iter_endpoints`, `_ensure_session`, `SIGNALING_DOMAINS`, `SIGNALING_PATHS`, `SUCCESS_CODES` | `pyagorartc.rtm.RtmRestClient.send_peer_message` (D18) |
| `agora_rtm.AgoraRTMSignaling.start_live`, heartbeat loop, `stop_live`, `send_ptz_ctrl`, `update_tokens` | stays (PetKit vocabulary, D18); rebuilt on `RtmRestClient` (§3.5) |
| `webrtc_common._resolve_agora_user_id` (unused) | the uid fallback in §3.2 |
| `webrtc_common._get_live_feed_for_webrtc`, `_missing_live_feed_fields`, `TEMP_CAMERA_TYPES` wake | stays (host glue) |
| `whep_proxy.py` views, auth, `PetkitGo2RTCProxyManager`, `go2rtc_stream.py`, the browser relay in `camera.py` | stays (host glue) |
| `const.AGORA_APP_ID` | stays in `const.py`; becomes `ChannelCredentials.app_id` and `RtmCredentials.app_id` |

### 3.2 Credentials

`LiveFeed` (`pypetkitapi` 1.29.0) plus `AGORA_APP_ID` map to both credential
types:

| Source | `ChannelCredentials` | `RtmCredentials` |
|---|---|---|
| `AGORA_APP_ID` | `app_id` | `app_id` |
| `channel_id` | `channel_name` | |
| `rtc_token` | `token` | |
| `uid` (may be `None`) | `uid` (must be an `int`) | |
| `app_rtm_user_id` | | `user_id` |
| `dev_rtm_user_id` | | `peer_user_id` |
| `rtm_token` | | `token` |

`LiveFeed.uid` is already filled from `app_rtm_user_id.split("_")[1]` by
pypetkitapi's validator when the payload lacks it. If it is still `None`,
that split failed. Today the host then sends JSON `null` as the AP uid.
`ChannelCredentials.uid` is an `int`, so the host must resolve it or stop.

```python
from pyagorartc import ChannelCredentials, RtmCredentials

from .const import AGORA_APP_ID


def petkit_credentials(feed: LiveFeed, account_user_id: str | None) -> tuple[ChannelCredentials, RtmCredentials]:
    uid = feed.uid
    if uid is None and account_user_id and account_user_id.isdigit():
        uid = int(account_user_id)  # the second tier of webrtc_common._resolve_agora_user_id
    if uid is None or not feed.channel_id or not feed.rtc_token:
        raise ValueError("live feed has no usable Agora uid, channel or token")
    channel = ChannelCredentials(app_id=AGORA_APP_ID, channel_name=feed.channel_id, token=feed.rtc_token, uid=uid)
    rtm = RtmCredentials(
        app_id=AGORA_APP_ID,
        user_id=feed.app_rtm_user_id,
        peer_user_id=feed.dev_rtm_user_id,
        token=feed.rtm_token,
    )
    return channel, rtm
```

Call it only after `_live_feed_ready_for_webrtc(feed)` is true, which
already guarantees the RTM fields. `ValueError` is caught by the WHEP view
today (502). `LiveFeed` carries no encryption fields, so `encryption` stays
`None`.

### 3.3 Edge discovery and ICE servers (`camera.py`)

`_refresh_agora_context` takes the credentials instead of the feed:

```python
async def _refresh_agora_context(self, creds: ChannelCredentials) -> None:
    self._agora_response = None
    async with AgoraAPClient(async_get_clientsession(self.hass)) as ap_client:
        self._agora_response = await ap_client.choose_server(creds)
    self._ice_servers = to_rtc_ice_servers(self._agora_response)
```

The AP call is TLS-verified now (D10); PetKit's gateway socket already was.

### 3.4 The session (`whep_proxy.py`)

`PetkitAgoraUpstreamManager.create_session` becomes:

```python
from pyagorartc import AgoraSession, CloseReason, PyAgoraRTCError, SessionOptions
from pyagorartc.rtm import RtmRestClient

PETKIT_OPTIONS = SessionOptions(
    client_codec="h264",
    instant_video=True,
    declare_remote_video_ssrc=True,   # go2rtc/pion needs a declared remote SSRC
    disable_audio=True,
    subscribe_retry_attempts=3,
    subscribe_retry_delay_s=1.0,
    send_set_client_role=True,        # until Q3 is answered on real hardware
    end_on_p2p_lost=True,             # PetKit ended the session on p2p_lost (D22)
)

async def create_session(self, camera, offer_sdp):
    device_id = str(camera.device.id)
    await self.close_session(device_id)
    live_feed = await camera.async_get_live_feed(refresh=True)
    if live_feed is None:
        raise RuntimeError("Live feed unavailable or missing RTM credentials")
    creds, rtm_creds = petkit_credentials(live_feed, camera.account_user_id)  # a property the host adds
    ap = await camera.async_refresh_agora_context(creds)

    rtm = PetkitLiveControl(RtmRestClient(rtm_creds, async_get_clientsession(self.hass)))

    async def refresh_rtc_token() -> str | None:
        feed = await camera.async_get_live_feed(refresh=True)
        if feed is None or not feed.rtc_token:
            return None
        rtm.update_tokens(feed)  # host rule, then RtmRestClient.update_token
        return feed.rtc_token

    async def on_closed(reason: CloseReason) -> None:
        if reason is not CloseReason.CLOSED_BY_HOST:
            await self.close_session(device_id)

    session = AgoraSession(
        creds,
        ap,
        options=PETKIT_OPTIONS,
        token_provider=refresh_rtc_token,
        on_closed=on_closed,
        spawn=lambda coro: self.hass.async_create_background_task(coro, f"petkit agora {device_id}"),
    )
    if not await rtm.start_live():
        LOGGER.warning("go2rtc upstream start_live/heartbeat not active for %s", device_id)

    session_id = secrets.token_hex(16)
    try:
        answer_sdp = await session.join(offer_sdp, session_id)
    except PyAgoraRTCError:
        await asyncio.gather(session.close(), rtm.stop_live(), return_exceptions=True)
        raise
    # ... store AgoraUpstreamSession(session=session, rtm=rtm, ...) as today ...
    return session_id, answer_sdp
```

- The `if not answer_sdp: raise RuntimeError(...)` branch goes: `join()`
  either answers or raises (D9).
- The inline-candidate loop goes: `join()` sends every `a=candidate:` in the
  offer itself. `filter_candidates` stays a helper for the host's own
  viewer-side filtering.
- The WHEP view catches `(OSError, RuntimeError, ValueError)`. Add
  `PyAgoraRTCError` to that tuple, or join failures become HTTP 500 instead
  of 502.
- `close_session` calls `await session.close()` in place of
  `agora_handler.disconnect()`. Calling it from inside `on_closed` is safe:
  `close()` does not cancel the task it runs in (D13).
- `on_peer_left` stays unset. PetKit had no `on_user_offline` handler; the
  library now unsubscribes a departed publisher itself. Re-sending
  `start_live` from `on_peer_left` is the natural recovery, untested.
- **PATCH trickle.** `add_session_candidates` parses with
  `parse_trickle_fragment` and returns 204, but a PATCH always arrives
  after the join, so the candidates are not forwarded (D11, Q4). Log the
  count and keep the endpoint; do not call `add_ice_candidate` there.

### 3.5 RTM control (host vocabulary on the library transport)

`RtmRestClient(creds, session=None, *, hosts=RTM_HOSTS)` sends one peer
message and returns the ack code. It raises `RtmError` when the peer does
not answer with an accepted code or no host delivers it. Endpoint rotation
(404, 429, 5xx move on; the last good host goes first next time) and the
three auth headers are the library's. The payload is serialised to the
compact JSON string Agora expects (protocol §8).

PetKit's controller keeps its commands, retries and cadence:

```python
START_LIVE = {"cmd": "start_live", "payload": {"isSD": 0}}

async def start_live(self) -> bool:
    for _ in range(START_LIVE_RETRIES):  # 5
        try:
            await self._rtm.send_peer_message(START_LIVE)  # wait_for_ack=True by default
        except RtmError as err:
            LOGGER.debug("start_live not acknowledged: %s", err)
            await asyncio.sleep(START_LIVE_RETRY_DELAY_SECONDS)  # 1.0
        else:
            self._start_heartbeat()
            return True
    return False
```

| Command | Call |
|---|---|
| `live_heartbeat` | `send_peer_message({"cmd": "live_heartbeat", "payload": {"isSD": 0}}, wait_for_ack=False)` every 0.5 s from a host task; stop after 10 consecutive `RtmError`s |
| `stop_live` | `send_peer_message({"cmd": "stop_live"}, wait_for_ack=False, accepted_codes=frozenset({"message_sent", "message_delivered", "message_offline"}))` |
| `ptz_ctrl` | `send_peer_message({"cmd": "ptz_ctrl", "payload": {"type": t, "ptz_dir": d}})` |

- Token refresh: `RtmRestClient.update_token(feed.rtm_token)`. The 20-minute
  `_refresh_tokens` loop stays in the host. Keep today's rule of refreshing
  only when `app_rtm_user_id` and `dev_rtm_user_id` are unchanged;
  `RtmCredentials` is frozen, so a changed user id means a new client.
- The heartbeat stays a host task, not the session's `keepalive`: it must
  start with `start_live`, before the join.
- `camera.py`'s own controller for manual start/stop and PTZ uses the same
  class with its own `RtmRestClient`.

### 3.6 Behaviour changes PetKit will see

| Change | Decision | What the host does |
|---|---|---|
| ORTC DTLS role `server` instead of `client` | D4 | Nothing expected; `ortc_dtls_role="client"` restores the old bytes if DTLS stalls (Q2). |
| Answer `a=setup` mirrors the gateway's DTLS role instead of a fixed `active` | D5 | Nothing; this is what the SDK does. |
| `leave` sent on close | D1 | Nothing. |
| `a=rtcp-fb` now reaches Agora; H265 is receive-only (`can_send`); ORTC layout pinned to HA-Luba's | D3, D1 | Watch for any change in what the camera publishes. |
| MID header extension stripped from the answer | D16 | Nothing expected; go2rtc demuxes by SSRC. |
| `enablePreallocPC: true` (was `false`); `sdk_version` 4.24.3 (was 4.24.0) | D7 | Nothing expected (Q10). |
| Renewal calls `token_provider` at most once per 30 s (was every ~1 s notice) | D8 | Each call was a PetKit API fetch; expect far fewer (Q11). |
| `on_closed` replaces `on_connection_lost`, is async, and also fires for gateway `quit` | D14 | Adapt the callback (§3.4). |
| `set_client_role` is off by default | D6 | PetKit must pass `send_set_client_role=True` until Q3 is answered. |
| `on_p2p_lost` is ignored by default | D22 | PetKit passes `end_on_p2p_lost=True` to keep its behaviour (Q13). |
| A failed join raises | D9 | Catch `PyAgoraRTCError` in the view. |
| Subscribe `rtx` follows the gateway's offer instead of always `true`; `on_add_video_stream` no longer requires `video` | D1 | Nothing expected. |
| `_online_users` cleared on close; one session per offer | D1, architecture §2 | Nothing; `create_session` already replaces the previous session. |
| AP call TLS-verified; a failed AP block no longer fails discovery | D10, D1 | Nothing. |
| RTM `send_peer_message` raises `RtmError` instead of returning `False` | D18 | Wrap calls as in §3.5. |
| Unchanged: nested `userAttributes` (D7), candidates in the join (D11), deferred answer with declared SSRC and its 15 s fallback, subscribe dedupe and retry | | |

### 3.7 Manifest and requirements

```json
"requirements": [
  "pypetkitapi==1.29.0",
  "aiofiles==24.1.0",
  "paho-mqtt==2.1.0",
  "pyagorartc==x.y.z"
]
```

Drop `websockets==15.0.1` and `sdp-transform==1.1.0`: after the migration
only pyagorartc imports them, and it declares both (§1.6). The exact
`websockets` pin is the one that would fight HA core.

### 3.8 Checklist

- [ ] Update `manifest.json` (§3.7).
- [ ] Add `petkit_credentials()` (§3.2); expose the account user id to it.
- [ ] `camera.py`: `_refresh_agora_context(creds)` on `AgoraAPClient`
      (§3.3); delete `_filter_candidates` / `filter_agora_candidates`.
- [ ] `whep_proxy.py`: rewrite `create_session` (§3.4); `close_session`
      uses `session.close()`; `AgoraUpstreamSession` holds `AgoraSession`;
      add `PyAgoraRTCError` to the WHEP view's catch.
- [ ] `whep_proxy.py`: `add_session_candidates` uses
      `parse_trickle_fragment` and does not forward (§3.4); delete
      `_parse_trickle_candidates`.
- [ ] `agora_rtm.py`: keep the controller, replace its HTTP layer with
      `RtmRestClient` (§3.5).
- [ ] `webrtc_common.py`: delete `_add_offer_candidates` and
      `_resolve_agora_user_id`.
- [ ] Delete `agora_api.py`, `agora_sdp.py`, `agora_websocket.py`.
- [ ] Run one session through go2rtc on each camera model with the options
      above before release, then the Q3 run (§4).

## 4. Open questions each host can close on real hardware

| Q | Mammotion | PetKit | How |
|---|---|---|---|
| Q2 DTLS role in the ORTC | yes | yes | One session with `SessionOptions(ortc_dtls_role=None)`. DTLS completes and video plays → the SDK's "send none" is safe. |
| Q3 `set_client_role` after join | — (mowers leave when it is sent, D6) | yes | One session with `send_set_client_role=False`. Video still flows after 10 s → drop the flag. |
| Q5 AP detail `8`/`4` TURN credentials | yes | yes | `get_ice_servers(strategy=TurnCredentialStrategy.DETAIL_FIRST)` with a relay-only viewer; a TURN 401 in the browser's ICE log answers it. |
| Q6 `openEncrypt` | yes | — (`LiveFeed` has no such field) | Log `openEncrypt` from every stream-token response for a while; a non-zero value is the answer, and a capture of that session is wanted. |
| Q10 `enablePreallocPC` / `enableInstantVideo` | `instant_video` on/off | both | Compare time to first frame. `enablePreallocPC` has no option today; answering it needs one. |
| Q11 renew debounce | yes | yes | Leave a session running past token expiry with DEBUG logs; count `renew_token` sends and any gateway error after repeats. Changing the 30 s needs `const.RENEW_TOKEN_DEBOUNCE_S` patched; there is no option. |

Report each result as a new decision in `decisions.md` that closes the
question.
