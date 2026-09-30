# pyagora divergence analysis (A / B / C)

## Path key

| Tag | Files |
|---|---|
| **A** (PyAgora, oldest) | `/home/michael/git/PyAgora/agora_api.py` (851 lines), `agora_sdp.py` (527), `agora_websockets.py` (1775). HEAD `429e2f4`. Working tree has one uncommitted change: `agora_websockets.py:444`, which comments out `await self._send_set_client_role(role="host", level=0)`. **At HEAD the call is still live.** |
| **B** (HA-Luba, Mammotion) | `/home/michael/git/HA-Luba/custom_components/mammotion/agora_api.py` (855), `agora_sdp.py` (533), `agora_websocket.py` (2061), `camera.py` (646), `coordinator.py:543-652` (the choose_server caller); token model `/home/michael/git/Luba-API/pymammotion/http/model/camera_stream.py:14-30` |
| **C** (PetKit) | `…/scratchpad/petkit/custom_components/petkit/agora_api.py` (465), `agora_sdp.py` (251), `agora_websocket.py` (1216), `agora_rtm.py` (394), `webrtc_common.py` (163), `camera.py` (679), `whep_proxy.py` (738), `go2rtc_stream.py` (505) |
| JS reference | `/home/michael/git/HA-Luba/agoraRTC_N-4.24.3.js`. Used below to decide conflicts. |

Short forms: `Bapi`, `Bsdp`, `Bws` are B's three modules, and the same pattern applies to A and C. `js:N` is a line in `agoraRTC_N-4.24.3.js`.

### Headline facts

1. **Neither A nor B can be imported as a library today.**
   - A: `Aws:18` imports `homeassistant.core`, and `Aws:26` imports `.coordinator`, a module PyAgora does not have.
   - B: `Bapi:120` annotates `-> AgoraResponse` without `from __future__ import annotations`. I verified that this raises `NameError: name 'AgoraResponse' is not defined` at import on CPython 3.13.8, while 3.14.5 works because of PEP 649. PyAgora's `pyproject.toml` declares `requires-python >=3.13`.
2. **The `api` modules are already host-clean in all three copies.** No `hass`, host model or host logger is imported. All coupling sits in the websocket module and its consumers.
3. **C's line-count shrinkage is mostly dead-code removal, plus one real regression.** C's `agora_sdp` parser never parses `a=rtcp-fb`, so the ORTC sent to Agora carries no RTCP feedback at all. I checked this by running both parsers on the same offer (see §1.2).
4. **The JS SDK decides several A/B vs C conflicts:**
   - C's `attributes: {userAttributes: {...}}` nesting matches `join_v3` in `js:46261-46343`. A and B send a flat `attributes` object.
   - The SDK renews with a new token supplied by the app (`js:45952`). C's `rtc_token_provider` does this. B resends the join token.
   - The SDK maps the gateway's DTLS role to the answer `setup` (`js:44242-44251`). B does this. A and C hard-code `active`.
   - The SDK sends `license` and `aes_mode`/`aes_secret`/`aes_salt` in `join_v3` (`js:46225`, `js:46356-46362`). No copy sends them, even though Mammotion's model carries `license`, `key`, `salt` and `openEncrypt`.

---

## 1. Module-by-module divergence

Kind column: **FIX** = bug fix, **VEND** = vendor-specific (M = Mammotion, P = PetKit), **REF** = refactor, **REG** = regression, **DEAD** = unreachable or unused.

### 1.1 `agora_api` (A 851 → B 855 → C 465)

A→B is typing and logging only, with two exceptions: B defaults `sid` in `update_ticket` (`Bapi:650-651`), and `_make_api_call` merges the primary and backup loops and logs each failure (`Bapi:798-807`). The A→B changes do not alter behaviour. The real differences are B vs C:

| # | Behaviour | A | B | C | Kind / notes |
|---|---|---|---|---|---|
| 1 | TURN credentials | uid-hash (`Aapi:~175`) | Ignores detail `8`/`4`; `username=str(uid)`, `credentials=sha256(uid)` (`Bapi:165-180`). The comment says detail creds cause TURN 401. | Three tiers (`Capi:107-116`): detail `8`/`4` → uid/sha256(uid) → `"test"`/`"111111"` | **Conflict.** C keeps the older HA-Luba strategy; the stale `/home/michael/git/HA-Luba/test_credential_fallback.py` still describes those three tiers and no longer matches B. JS derives from uid when `ENCRYPT_PROXY_USERNAME_AND_PSW` is set, else uses the SDK default. Needs a `credential_strategy` knob, or a check of whether PetKit's AP response even populates 8/4. |
| 2 | Non-zero `buffer.code` | raise | raise `Exception` (`Bapi:149-150`) | skip that buffer; raise only if none succeeded (`Capi:88-95,154-155`) | **FIX in C.** One failed flag, e.g. TURN, no longer kills gateway discovery. |
| 3 | Edge entries missing ip/port | KeyError | KeyError (`edge["ip"]`, `Bapi:184`) | filtered out (`Capi:140`) | FIX (C) |
| 4 | `responses` field | only when >1 flag (`Bapi:234`) | same | always set (`Capi:172`) | REF. `get_*_addresses` still work either way. |
| 5 | Primary response pick | flag 4096, else first flag (`Bapi:215-218`); scalar fields taken from `first_buffer`, which can disagree with `addresses` | same | flag 4096, else first; every field from the same block (`Capi:157-173`) | **FIX (C).** In B, if flag 26 came first, `ticket`/`uid` belonged to TURN while `addresses` were the gateway's. |
| 6 | Request `detail` | `{11,17,22}` | `{11,17,22}` (`Bapi:739-744`); `"6"` commented out (`Bapi:738`) | adds `"6": string_uid` (`Capi:375`) | C matches JS (the websocket join `details.6 = stringUid`, `js:46240`). Probably harmless for both. |
| 7 | Default `service_flags` | `[11, 26]` (`Bapi:580`) | same | same, via `SERVICE_IDS` (`Capi:322-326`) | — |
| 8 | `get_ice_servers` default | `use_all_turn_servers=True` | `True` (`Bapi:238`) | `False` (`Capi:203`) | Both hosts pass `False` explicitly (`coordinator.py:623`, `camera.py:121`, `C camera.py:638`). Pick one default. |
| 9 | ICE-server validation logging | yes | "CRITICAL" empty-credential logs and summary (`Bapi:284-346`) | none | REF (C drops the logs) |
| 10 | `update_ticket` (AP uri 28) | yes | yes (`Bapi:606-673`), **no caller in B** | removed | DEAD in B; C loses nothing it used. Worth keeping in the library: the JS SDK uses it on ticket refresh. |
| 11 | `get_turn_server_config` (JS `turnServer` shape incl. `serversFromGateway` = gateway port+30, password = token) | yes | yes (`Bapi:350-405`), **no caller** | removed | DEAD. It is the only encoding of the gateway-derived TURN rule (port+30, JWT password), so it is worth preserving as a documented pure function. |
| 12 | `EdgeAddress.to_dict` / `ICEServer.to_dict` | yes | yes (`Bapi:69-94`), unused | removed | DEAD |
| 13 | `merge_objects` public vs private | public static | public (`Bapi:676`) | `_merge_objects` (`Capi:346`) | REF |
| 14 | Random ids | `random.randint` | `randint` (`Bapi:583,732`) | `secrets.randbelow` (`Capi:328,368`) | REF |
| 15 | Endpoint failure handling | three `except` clauses incl. bare `Exception` | `except Exception` + debug log (`Bapi:804-807`) | `(TimeoutError, ClientError, ValueError)` (`Capi:423`); non-200 raises `ValueError` (`Capi:460-464`) | C is narrower: a JSON `TypeError`/`KeyError` escapes the fallback loop. |
| 16 | Owned-session lifetime | new `ClientSession` per call, closed | same (`Bapi:793-813`) | creates one lazily, caches it on `self.session`, closes only in `__aexit__` (`Capi:404-409`) | REF. Leaks if used without `async with`; both hosts use `async with`. |
| 17 | TLS on AP call | `ssl=False` | `ssl=False` (`Bapi:849`) | `ssl=False` (`Capi:458`) | Same in all three; should become verified. |
| 18 | `from __future__ import annotations` | string forward ref | **absent → breaks on 3.13** | present (`Capi:3`) | **REG in B** for the library's 3.13 floor |

**What C's `agora_api` shrinkage removed:** docstrings and logging (~150 lines), `update_ticket` (~70), `get_turn_server_config` (~55), the two `to_dict` methods (~25), and ICE validation (~40). Net loss: nothing either host calls. Material worth keeping for a library: `update_ticket` and the `serversFromGateway` rule in `get_turn_server_config`.

### 1.2 `agora_sdp` (A 527 → B 533 → C 251)

A→B: B parses the RFC 5285 extmap `/direction` suffix (`Bsdp:115-121`); A would `int("2/recvonly")` and crash. B also drops an unused `cname` local (A:369). The rest is `lines.extend` refactors.

I ran B's and C's `parse_offer_to_ortc` on one Chrome-style offer (audio 111 opus; video 96 VP8 + 97 rtx + 49 H265 profile-id=1; both `a=recvonly`; `a=rtcp-fb:96 nack pli`; sdes:mid extmap). Results:

| Probe | B | C |
|---|---|---|
| `a=recvonly` captured | **No** (`[None, None]`) | Yes |
| dtlsParameters.role | `"server"` | `"client"` |
| caps layout | `sendrecv: {111, 96, 97}`, `recv: {49}` (H265 is not sendable) | `recv: {111, 96, 97, 49}`, `send: {}` |
| rtcpFeedbacks of PT 96 | `[nack pli, rrtr]` | **`[]`** |
| sdes:mid ext in ORTC | present | present |

| # | Behaviour | A/B | C | Kind |
|---|---|---|---|---|
| 1 | Parser structure | one if/elif chain (`Bsdp:12-134`) | dispatch helpers + `match` (`Csdp:11-123`) | REF |
| 2 | Direction attributes | `elif attr == "direction"` (`Bsdp:81-82`) never matches, because real SDP writes `a=recvonly`. **Direction is never parsed.** | `Csdp:78-80` handles all four | **FIX in C**. Latent BUG in A/B, masked because B builds its answer from a second, `sdp_transform` parse (`Bws:1194-1408`). |
| 3 | `a=rtcp-fb` | parsed (`Bsdp:102-112`) | **not parsed at all**: no `case` in `Csdp:82-123` | **REG in C.** Agora never learns the browser/go2rtc supports nack/pli/transport-cc/goog-remb. |
| 4 | Forced `rrtr` per codec | yes (`Bsdp:310-312`) | no | JS-parity item lost in C |
| 5 | `can_send` codec filter (H265 never sendable; VP9 profile 1/3, AV1 profile 1 recv-only) | yes (`Bsdp:216-231`), routes those to `recv` | removed | Lost in C. **Relevant to Mammotion**: the mower publishes H265 (`Bws:786-793`). |
| 6 | Caps buckets | `send`/`recv`/`sendrecv`; everything sendable goes into `sendrecv` regardless of m-line direction (`Bsdp:250-355`) | `send`/`recv` chosen by direction (`Csdp:228-241`); no `sendrecv` key | VEND-ish: C's offers come from go2rtc (recvonly). Needs a JS getOrtc check (`js:~43950-44145`). |
| 7 | Extensions bucket | always `sendrecv` (`Bsdp:350-355`) | follow the direction targets | same as 6 |
| 8 | fmtp key-only params (e.g. RED `111/111`) | kept as `None` (`Bsdp:321-324`) | dropped (`Csdp:213-214`) | minor loss in C |
| 9 | `encodingParameters` | set only when present (`Bsdp:299-300`) | always present, may be JSON `null` (`Csdp:194`) | wire-shape difference |
| 10 | ICE/DTLS precedence | session level first, then first media (`Bsdp:234-282`) | first media first, then session (`Csdp:133-165`) | REF, same result in practice |
| 11 | DTLS role in ORTC | `"server"` (`Asdp:239,274`; `Bsdp:246,281`) | `"client"` (`Csdp:167`), set even when there are no fingerprints | **Conflict.** Memory fix #2 says `server`. JS `f2()` (`js:44146-44181`) sends **no role at all**. See §1.3 row 9. |
| 12 | `SDPParser.write` + `generate_answer_from_ortc` (opus stereo forcing, ice-lite, msidSemantic) | present (`Bsdp:136-206,365-533`), **unused**: the call site is commented out at `Bws:641` | removed | DEAD. Nothing lost in behaviour; the answer is generated in the websocket module in all three copies. |
| 13 | MID-extension stripping | not in `parse_offer_to_ortc` | not there | Memory fix #1 says "both ORTC and answer". **No copy strips it from the ORTC**; A and B strip it only from the answer (§1.3 row 13). |

**What C's `agora_sdp` shrinkage removed:** the dead writer and answer generator (~280 lines), `can_send`, forced `rrtr`, key-only fmtp, extmap-direction capture, and **the rtcp-fb parser**. The last is a real loss. The rest either was dead or is a JS-parity item C chose not to carry.

### 1.3 `agora_websocket(s)` (A 1775 → B 2061 → C 1216)

A→B added: the four constructor callbacks and options (`Bws:121-149`), the renew debounce (`Bws:47,528-561`), the FPV keep-alive loop (`Bws:52,486-526`), `on_notification` quit handling (`Bws:705-720`), the `target_uid` filter (`Bws:746,774,836`), peer recovery with cooldown, attempt cap and reset (`Bws:102-114,861-926`), the `CLIENT_CODEC` constant (`Bws:119`), RTX gating from the gateway caps (`Bws:630-636,1086`), the pt=0 H265 warning (`Bws:786-793`), setup mirrored from the gateway DTLS role (`Bws:1534-1542`; A hard-codes `active` at `Aws:1257`), and cancellation of the new tasks in `disconnect` (`Bws:2005-2011`). Everything else A→B is log levels and `contextlib.suppress`.

B vs C:

| # | Behaviour | B (A where different) | C | Kind |
|---|---|---|---|---|
| 1 | Constructor | `(hass, recover_stream, keepalive, target_uid, session_ended)` (`Bws:121-128`) | `(rtc_token_provider, *, prefer_instant_video, subscribe_retry_delay, subscribe_retry_attempts, declare_remote_video_ssrc, disable_audio_answer, on_connection_lost)` (`Cws:58-68`) | Disjoint extension surfaces (§4) |
| 2 | Task spawning | `asyncio.ensure_future` for loops; `hass.async_create_task` for the fpv budget disconnect, p2p restart, session_ended and peer recovery (`Bws:522,697,720,873`) | `asyncio.create_task` everywhere; no hass | C is already host-neutral here |
| 3 | `connect_and_join` signature | `(agora_data: StreamSubscriptionResponse, offer_sdp, session_id, agora_response)` (`Bws:207-213`) | `(live_feed: LiveFeed, offer_sdp, session_id, app_id, agora_response)` (`Cws:114-121`). `app_id` is separate because LiveFeed has none. | Replace with a neutral credentials object (§3) |
| 4 | Re-entrancy guard | disconnects first when already joined (`Bws:224-230`) | none | **REG in C** (fix #4's spirit) |
| 5 | WS TLS | `CERT_NONE`, no hostname check (`Bws:33-41`) | verified, TLS ≥1.2 (`Cws:28-33`) | **FIX in C**. It proves `<ip-dashed>.edge.agora.io` presents a valid cert. |
| 6 | Browser candidates → ORTC | collected into `self.candidates`; `_convert_candidates_to_ortc` exists (`Bws:1095-1145`) but is **never called**, so candidates are never sent | converted and put in `ortc.iceParameters.candidates` before join (`Cws:135-145`) | C is the working implementation. B's trickle collection (`camera.py:308-317`) is vestigial, which is fine because the gateway is ice-lite and its candidates are in the answer. JS `f2` sends none. |
| 7 | join_v3 `attributes` | flat: `"attributes": {enableAudioMetadata…}` (`Bws:991-1015`, `Aws:733`) | nested: `"attributes": {"userAttributes": {…}}` (`Cws:688-709`) | **C matches JS** (`js:46261-46343`). A and B deviate but work, so the gateway is presumably lenient. |
| 8 | join_v3 fields | `sdk_version 4.24.3`, `codec vp8`, `enablePreallocPC True`, `enableInstantVideo False`, plus `enableNetworkQualityProbe/enableUserList/enableVosFallback/enableQualityFallback/enableDualStreamFlag` | `sdk_version 4.24.0`, `codec h264`, `enablePreallocPC False`, `enableInstantVideo` configurable (`Cws:696`), without those five keys | VEND (codec: M vp8 vs P h264); the rest is drift. JS forces `enablePreallocPC = true` (`js:41510`). **None send `license`, `details.6`, `string_uid` or `aes_*`** (JS `js:46225-46362`). |
| 9 | Answer `a=setup` | mirrors the gateway role: `server`→`passive`, else `active` (`Bws:1538-1539`), exactly JS `S2()` (`js:44242-44251`, which also maps `auto`→`actpass`) | hard-coded `active` (`Cws:945`), as A does (`Aws:1257`) | **B is correct per JS.** C's ORTC role `client` combined with answer `active` would declare both sides DTLS client if the gateway honoured the ORTC role. It evidently doesn't, but that is luck. |
| 10 | `set_client_role(host, level=0)` after join | removed (the method remains dead at `Bws:1021-1047`). A HEAD calls it; the working tree comments it at `Aws:444`. | **called** (`Cws:354`) | **Conflict / fix #3.** For Mammotion it makes the mower leave about 500 ms after video starts. PetKit presumably tolerates or needs it. Must be an option, off by default. |
| 11 | Existing streams in the join payload | not handled; relies on `on_add_video_stream` | `_find_existing_video_streams` walks the join payload and subscribes (`Cws:555-614`) | FIX / feature in C (generic) |
| 12 | Deferred answer until `on_add_video_stream` | no | `declare_remote_video_ssrc` holds the answer until an SSRC is known, then emits `a=ssrc`/`ssrc-group:FID`/`a=msid:agora agora-video`; on the 15 s timeout it falls back to an early answer (`Cws:393-403,464-489,250-259,1005-1029`) | VEND-P (a go2rtc/pion consumer needs declared SSRCs); generic as an option |
| 13 | MID extension in answer | skipped (`Bws:1611-1615`, `Aws:1327`) | **not skipped** (`Cws:950-963`) | **Fix #1 missing in C.** It may not bite PetKit if its m-line MIDs happen to match the gateway's, but it is a hazard. |
| 14 | `msid` in answer | none. B allocates `_msid_stream_id=1` and UUID track ids (`Bws:174-177,235-238`) but **never writes them**. | only with `declare_remote_video_ssrc`, stream id `agora` (`Cws:1020-1022`) | **Fix #5 is in no copy.** B carries dead state for it. |
| 15 | Fingerprint read from gateway ORTC | `fp.get("algorithm","sha-256")` (`Bws:1482-1485`); the gateway does use `algorithm` (`js:44232`) | `hashFunction or algorithm` (`Cws:832-841`) | C is more tolerant; both correct for the gateway |
| 16 | Fingerprint injection source | `agora_response.addresses` (`Bws:606`) | `get_gateway_addresses() or addresses` (`Cws:367-369`) | equivalent |
| 17 | Answer when the gateway gives no codecs for an m-line | emits `m=audio 9 … ` with an empty PT list, which is invalid SDP (`Bws:1580-1584`) | falls back to the offer's payloads (`Cws:916-924`) | **FIX in C** |
| 18 | `_validate_sdp` | requires ≥2 m-lines (`Bws:1731`) | ≥1 (`Cws:1165`) | VEND-P (audio can be disabled) / generalisation |
| 19 | Audio disabled | no | `disable_audio_answer` → `a=inactive` (`Cws:1047-1048`) | VEND-P option |
| 20 | Join timeout or failure | returns a **fabricated fallback SDP** with random ICE creds and fingerprint (`Bws:395-399,1740-1838`), so the viewer gets an "answer" that can never connect | returns `None` (`Cws:265`) | **BUG in A/B, fixed in C** |
| 21 | Subscribe | codec `CLIENT_CODEC` = vp8, same as the join; `rtx` = whether the gateway offered rtx (`Bws:1049-1093`) | codec `h264`, `rtx=True` always; deduped per `(uid, ssrc)` (`Cws:509-553`); retry loop (`Cws:616-646`) | B's RTX gating is FIX (generic); C's dedupe and retry are generic features; the codec is VEND |
| 22 | `on_add_video_stream` gate | no `video` flag required (`Bws:765-775`, deliberate) | requires `message["video"]` truthy (`Cws:447-450`) | Conflict; B's rule is the more permissive |
| 23 | `on_user_online` | subscribes a pending stream (`Bws:753-763`) | only tracks the uid (`Cws:433-438`) | C loses the subscribe-after-online ordering fix |
| 24 | `on_user_offline` | unsubscribe, renew, peer recovery (`Bws:825-859`) | **no handler** | M-specific recovery; unsubscribe/cleanup is generic and lost in C |
| 25 | `target_uid` filtering (multi-camera Yuka/Luba2) | yes | no | generic feature |
| 26 | `on_notification` quit (code 2003 repeat join) | → `session_ended` (`Bws:705-720`) | not handled | generic |
| 27 | `on_p2p_lost` | handler commented out (`Bws:198`); `_restart_websocket` (`Bws:1951-1994`) therefore DEAD | registered: disconnect + `on_connection_lost` (`Cws:103,414-422`) | C is live; B deliberately disabled restart |
| 28 | WS closed in message loop | sets state only | fires `on_connection_lost` once (`Cws:292-294,1173-1177`) | generic |
| 29 | renew_token | debounced 30 s, resends the **join token** (`Bws:537-555`) | no debounce; asks `rtc_token_provider` for a fresh token (`Cws:316-339`) | JS sends a new token (`js:45952`). Combine C's provider with B's debounce. |
| 30 | `on_token_privilege_did_expire` | clears the debounce (`Bws:446-451`) | log only | — |
| 31 | FPV keep-alive + `availableTime` budget | yes (`Bws:486-526`) | no (PetKit's analogue is RTM `live_heartbeat`, §5) | VEND-M |
| 32 | Peer recovery | yes (`Bws:861-926`) | no | VEND-M policy on a generic hook |
| 33 | `disconnect` | sends `{"_type":"leave"}` when joined (`Bws:2016-2021`); does not await cancelled tasks; clears `_online_users`/`_video_streams` (`Bws:2031-2032`) | **no `leave`**; awaits cancelled tasks except the current one (`Cws:1181-1203`); clears `_video_streams`/`_subscribed_video_streams` but **not `_online_users`** (`Cws:1215-1216`) | C: REG (no leave, fix #4 partial); FIX (awaited cancellation, self-cancel guard) |
| 34 | CancelledError in loops | swallowed (`Bws:456-457,483-484`) | re-raised (`Cws:289-291,310-312`) | FIX in C |
| 35 | Dead code | `_response_handlers` (never populated), `_add_candidates_to_sdp` (`Bws:1147-1191`), `_get_agora_edge_services` + `ResponseInfo`/`AddressEntry` (`Bws:55-77,1840-1944`), `is_ipv4` (`Bws:2038-2061`), `_restart_websocket`, `_send_set_client_role`, `_rejoin_token/_vid/_cid/_answer_sdp` stored but unread, `self._sdp_info = ortc_info` (type mismatch, unread, `Bws:281`), msid fields | `_resolve_agora_user_id` and `_add_offer_candidates` in `webrtc_common.py:35-102` are unused | **This is most of C's 845-line reduction**, together with removing the ~280-line fallback-SDP code and the uri_mappings/codec-default block in `_parse_offer_sdp` (`Bws:1246-1353`) |
| 36 | `SdpInfo` | 13 fields incl. codecs and candidates (`Bws:80-96`) | `OfferSdpInfo`, 10 fields (`Cws:39-52`) | REF; the removed fields were never read by the answer builder |
| 37 | Default rtcp-fb injection in `_parse_offer_sdp` | yes (`Bws:1293-1313`), but feeds only `SdpInfo` codecs, which the answer never reads | removed | DEAD in B |

### 1.4 Memory fix checklist (`project_agora_webrtc_fixes.md`)

| Fix | A | B | C |
|---|---|---|---|
| 1 MID ext stripped (memory says ORTC + answer) | answer only (`Aws:1327`) | answer only (`Bws:1611`) | **neither** |
| 2 DTLS `role:"server"` in ORTC | yes (`Asdp:239,274`) | yes (`Bsdp:246,281`) | **no**, `"client"` (`Csdp:167`). JS sends none (`js:44146-44181`). |
| 3 No `set_client_role` | HEAD calls it; working tree comments it out (`Aws:444`) | yes (removed) | **calls it** (`Cws:354`) |
| 4 `disconnect` clears `_online_users` + `_video_streams` | yes (`Aws:1746-1747`) | yes (`Bws:2031-2032`) | partial: `_online_users` not cleared |
| 5 msid stream id `1` | **no** (fields allocated only, `Aws:116-118,163-165`) | **no** (same, dead) | no (`agora` stream id, opt-in) |
| 6 FPV 4G keep-alive | no | yes (`Bws:486-526`), 3 s cadence, not 5 s as memory says | no (RTM heartbeat instead) |
| 7 Peer-leave recovery | no | yes, plus attempt cap/reset beyond what memory describes | no |

---

## 2. Host coupling inventory

| Reference | Where | What is actually used | Neutral replacement |
|---|---|---|---|
| `homeassistant.core.HomeAssistant` | `Bws:20,123,145`; `Aws:18,89` | only `self.hass.async_create_task(...)` at `Bws:522` (fpv budget → `disconnect`), `697` (p2p restart, dead), `720` (`session_ended`), `873` (peer recovery). Never `hass.loop`, never state. Tests already pass `MagicMock()` as hass (`tests_ha/test_agora_camera_uid_filter.py:19`, `test_agora_session_quit.py:45`). | `asyncio.get_running_loop().create_task` with a strong-ref `set[Task]` and done-callback discard. Optionally an injectable `spawn: Callable[[Coroutine], Task]` so HA can pass `hass.async_create_background_task`. |
| `StreamSubscriptionResponse` | `Bws:28` (imported via `.coordinator`, a re-export); `camera.py:34-36`; `coordinator.py:80` | reads `.appid` `.token` `.channelName` in the join (`Bws:977-979`), `.token` in renew (`Bws:553`), `.availableTime` for the FPV budget (`Bws:313`); dead `_get_agora_edge_services` reads `.uid` too (`Bws:1844-1847`). Coordinator converts it with `to_dict()` for `choose_server` (`coordinator.py:609-615`). | `ChannelCredentials` dataclass (§3) |
| `.coordinator` | `Bws:28`, `Aws:26` | only the model re-export | removed |
| `pypetkitapi.LiveFeed` | `Cws:16,116`; `agora_rtm.py:11`; `camera.py:18-27` | `.rtc_token` (`Cws:123,669`), `.channel_id` (`Cws:670`); RTM reads `.app_rtm_user_id` `.dev_rtm_user_id` `.rtm_token` (`agora_rtm.py:120-122`); camera reads `.uid` (`camera.py:630`) | `ChannelCredentials` + `RtmCredentials` |
| `pypetkitapi.TEMP_CAMERA_TYPES` | `webrtc_common.py:8` | device wake policy | stays in the integration |
| `pymammotion` | `camera.py:34-38`, `coordinator.py` | only in consumers; the Agora modules never import it | — |
| `.const` / host `LOGGER` | `agora_rtm.py:13`, `webrtc_common.py:11`, `whep_proxy.py:25` (`AGORA_APP_ID, DOMAIN, LOGGER`), `go2rtc_stream.py:19` | logger; `AGORA_APP_ID` (`const.py:14` = `244c4995…e410`, a PetKit constant) | `logging.getLogger(__name__)`; app id becomes a field of the credentials |
| `webrtc_models.RTCIceCandidateInit` | `Bws:22,154,1148,2034`; `Cws:18,76,110`; `whep_proxy.py:15`; `webrtc_common.py:9` | `.candidate` (str) only in the handler; `sdp_mid`/`sdp_m_line_index` are set by C's producers but never read | a library `IceCandidate` dataclass (`candidate: str, sdp_mid: str \| None, sdp_mline_index: int \| None`), or accept plain `str` |
| `webrtc_models.RTCIceServer` | `camera.py:39,116`; `coordinator.py:117,627`; `C camera.py:28,639` | only in hosts, converting the library's `ICEServer` | the library returns its own `ICEServer`; the host converts |
| `homeassistant.components.camera/web_rtc` | `camera.py` (both), `whep_proxy.py:17-21`, `go2rtc_stream.py:13-17` | HA glue | stays |
| `go2rtc_client.ws` | `C camera.py:11-17` | browser ↔ go2rtc relay | stays |

The `api` and `sdp` modules have **zero** host references in A, B and C.

---

## 3. Credential / token model

### Mammotion `StreamSubscriptionResponse` (`camera_stream.py:14-30`)

| Field | Read by Agora code? | Notes |
|---|---|---|
| `appid: str` | yes: join `app_id`, choose_server | |
| `channelName: str` | yes: join `channel_name`, AP `cname` | |
| `token: str` | yes: join `channel_key`, AP `key`, renew | |
| `uid: int` | yes: AP `uid` (`coordinator.py:614`) → TURN username and password | the viewer uid, shared by every camera token (`Bws:710-711`) |
| `cameras: list[Camera{cameraId:int, token:str}]` | **no** | per-camera tokens are unused; the camera is chosen by `target_uid` = slot+1 (`camera.py:61-85`) |
| `areaCode: str` | **no**. AP `area_code` stays the default `"CN,GLOBAL"` (`Bapi:548`). | candidate for request detail `11`/`22` |
| `license: str \| None` | **no** | JS sends `license` in `join_v3` (`js:46225`) |
| `availableTime: int \| None` | yes: FPV budget deadline (`Bws:499-503`) | Mammotion 4G quota |
| `openEncrypt: int`, `key: str \| None`, `salt: str \| None` (AES_256_GCM2; salt is base64) | **no** | JS: `aes_mode`, `aes_secret`, `aes_salt`, `aes_encrypt` (`js:46356-46362`). The protobuf `SignalRequest` also has them (`js` b0 descriptor). **Encrypted channels are unsupported in every copy.** |

### PetKit `LiveFeed` (pypetkitapi 1.29.0; source read via `uv run --with pypetkitapi==1.29.0`)

The task said this package was unavailable, but `uv` installed it, so the fields below come from the source.

| Field | Read? | Notes |
|---|---|---|
| `channel_id` (`channelId`) | yes: join `channel_name`, AP `cname` | |
| `rtc_token` (`rtcToken`) | yes: join `channel_key`, AP `key`, renew (via provider) | |
| `uid: int \| None` | yes: AP `user_id` (`C camera.py:630`) | derived from `app_rtm_user_id.split("_")[1]` when absent; **may be `None`**, which would put JSON `null` into the AP `uid` |
| `app_rtm_user_id`, `dev_rtm_user_id`, `rtm_token` | RTM only (`agora_rtm.py:120-122`) | |
| app id | not in the model: `const.AGORA_APP_ID` (`const.py:14`) | |

### Proposed neutral model

```python
@dataclass(frozen=True, kw_only=True)
class ChannelCredentials:
    app_id: str                      # M: appid          P: const AGORA_APP_ID
    channel_name: str                # M: channelName    P: channel_id
    token: str                       # M: token          P: rtc_token
    uid: int                         # M: uid            P: uid (host must resolve None)
    string_uid: str | None = None    # JS details.6 / string_uid; optional
    area_code: str = "CN,GLOBAL"     # M: areaCode (currently ignored); AP detail 11/22
    license: str | None = None       # M: license; JS join_v3 `license`
    encryption: ChannelEncryption | None = None
    # Vendor-specific, kept out of the signalling path:
    # availableTime (M) belongs to the host's keep-alive policy, not here.

@dataclass(frozen=True, kw_only=True)
class ChannelEncryption:             # M only today (openEncrypt != 0)
    mode: str                        # JS aes_mode, e.g. "aes-256-gcm2"; map from openEncrypt
    secret: str                      # M: key (verbatim)
    salt: bytes | None               # M: base64-decoded salt

@dataclass(frozen=True, kw_only=True)
class RtmCredentials:                # P only
    app_id: str
    app_user_id: str                 # app_rtm_user_id
    peer_user_id: str                # dev_rtm_user_id
    token: str                       # rtm_token
```

Token refresh stays out of the dataclass. It is injected as `token_provider: Callable[[], Awaitable[str | None]]` (C's pattern), so the frozen credentials stay immutable. `cameras[]` stays host-side. `target_uid` is a session option, not a credential.

---

## 4. Callback and extension points

| Point | B | C | Generic or vendor |
|---|---|---|---|
| Token renewal | resends the stored token, 30 s debounce | `rtc_token_provider` → fresh token, no debounce | **Generic.** Merge: provider plus debounce. `on_token_privilege_will_expire` fires every ~1 s (`Bws:43-47`). |
| Peer left → recover | `recover_stream` after 2 s debounce, 15 s cooldown, 5-attempt cap, 600 s reset (`Bws:102-114,861-926`). Host does a BLE sync and a fresh subscription (`camera.py:388-400`). | — | Hook generic (`on_peer_left(uid)` / debounced `recover`); the policy is M |
| Encoder keep-alive | `keepalive() -> bool` every 3 s; `False` stops it; `availableTime` deadline → disconnect (`Bws:486-526`); host sends MQTT `refresh_fpv` on 4G (`camera.py:375-386`) | — (RTM `live_heartbeat` every 0.5 s is P's equivalent, outside the handler) | Generic "periodic callback while joined" with an optional deadline; content is vendor-specific |
| Session ended by gateway | `session_ended` on `on_notification action=quit` (2003) | — | Generic |
| Connection lost | — (p2p_lost disabled) | `on_connection_lost` (sync, fire-once) on `on_p2p_lost` or WS close | Generic. Unify as one `on_closed(reason)`. |
| Candidate handling | `candidates` list mutated directly by the host (`camera.py:267,317`); never sent | `add_ice_candidate`; converted into the join ORTC; host pre-filters to srflx/prflx/relay matching TURN IPs (`C camera.py:654-679`); trickle PATCH after join is accepted but **never forwarded** (`whep_proxy.py:257-280`) | Generic: `add_ice_candidate` before join. The filter is a pure helper. No copy implements a post-join trickle message. |
| ICE server selection | `get_ice_servers(use_all_turn_servers=False)` → 3 entries (udp/tcp/turns) for the first TURN address | same | Generic, pure |
| TURN mode | `new_turn_mode` 1/2/3/4 (`Bapi:298-324`) | same | Generic |
| TURN credential strategy | uid-hash only | detail 8/4 → uid-hash → test/111111 | Generic knob; default undecided (§1.1 row 1) |
| Encryption | none | none | Generic (JS fields), used by M only |
| Target uid filter | `target_uid` | — | Generic |
| Client codec | `vp8` | `h264` | Per-session option |
| set_client_role after join | off | on | Option; default off (M breaks) |
| Instant video / deferred SSRC answer / subscribe retry / disable audio | — | options (`Cws:62-66`) | Generic options; P needs them for go2rtc |

---

## 5. PetKit-only capabilities

**`agora_rtm.py`** is the Agora RTM (Signaling 1.x) REST peer-message client, not the RTM websocket SDK.
- Endpoint: `POST https://{api.agora.io|api.sd-rtn.com}/dev/v2/project/{app_id}/rtm/users/{url-quoted app_user_id}/peer_messages[?wait_for_ack=true]` (`agora_rtm.py:21-27,229-237`).
- Headers: `x-agora-token`, `x-agora-uid`, `Authorization: agora token=<rtm_token>` (`:220-225`).
- Body: `{"destination": dev_user_id, "enable_offline_messaging": false, "enable_historical_messaging": false, "payload": "<compact JSON>"}` (`:213-218`).
- Success: `result == "success"` and `code ∈ {message_sent, message_delivered}` (plus `message_offline` for stop) (`:28-36,300`).
- Retries: 404 or 5xx/429 moves to the next endpoint, and the last good endpoint is remembered (`:267-279,321-333`).
- PetKit command vocabulary: `start_live {isSD}` ×5 retries at 1 s (`:159-188`), `live_heartbeat {isSD}` every 0.5 s, stopping after 10 failures (`:341-368`), `stop_live` (`:378-385`), `ptz_ctrl {type, ptz_dir}` (`:87-99`).
- Split: the REST transport (endpoint rotation, auth headers, ack semantics, `send_peer_message`) is **generic Agora** and fits pyagora as `pyagora.rtm.RtmRestClient`. The `start_live`/heartbeat/`ptz` vocabulary is **PetKit protocol** and stays in the integration or its vendor lib.

**`whep_proxy.py`** has two managers plus HA views.
- `PetkitAgoraUpstreamManager` (`:113-296`) turns a WHEP offer (from go2rtc) into an Agora answer: it refreshes the live feed, calls choose_server, extracts inline `a=candidate` lines, filters them, starts RTM, runs `connect_and_join` with the P options, and builds a location path and a 20-minute RTM token refresh loop.
  - The core ("answer an arbitrary offer SDP by joining Agora, including non-trickle inline candidates, and return answer SDP plus a session handle; PATCH candidates; DELETE") is **reusable library material**, as a host-neutral `AgoraSession`.
  - Per-device bookkeeping, `hass.data` registries, the aiohttp views and RTM coupling are integration glue.
- `_parse_trickle_candidates` (`:509-541`) is a pure WHEP-PATCH fragment → candidates helper (reusable).
- `PetkitGo2RTCProxyManager` (`:299-457`) is a plain HTTP reverse proxy to go2rtc's `/api/webrtc?src=` (glue).
- Auth: `_check_external_auth`/`_validate_signed_request` (`:38-89`) use HA JWT signing (glue).

**`go2rtc_stream.py`** is entirely glue. It registers the go2rtc stream `petkit_{id}` whose source is `webrtc:{ha_url}{signed /api/petkit/whep_upstream/{id}}` via `/api/streams` POST/PUT/PATCH fallbacks, discovers the RTSP URL from `/api`, and migrates legacy sources. It contains no Agora or WebRTC negotiation; go2rtc itself acts as the WHEP client.

**`camera.py` (C)** relays browser ↔ go2rtc over `go2rtc_client.ws` with PENDING/ACTIVE candidate buffering (glue). `_filter_candidates` (`:654-671`) is a pure helper for the library.

**Belongs in pyagora:** the AP client, SDP↔ORTC, the answer builder, the WS session (with C's options), the candidate filter, WHEP-fragment candidate parsing, and the RTM REST transport. **Stays in integrations:** the HA views/auth, go2rtc stream management, the browser relay, PetKit RTM command vocabulary and wake/refresh (`webrtc_common.py`), and Mammotion FPV/BLE-sync recovery policies.

---

## 6. Public interface hosts need (derived from call sites)

Call sites:
- B: `camera.py:158-164` (construct), `:267`/`:317` (candidates), `:427-429` (`connect_and_join`), `:338`/`:365` (`disconnect`); `coordinator.py:610-638` (`AgoraAPIClient().choose_server`, `get_ice_servers`); `camera.py:121`.
- C: `whep_proxy.py:154-190` (construct, `add_ice_candidate`, `.candidates =`, `connect_and_join`), `:193,240` (`disconnect`); `camera.py:625-646` (`choose_server`, `get_ice_servers`), `:660` (`get_turn_addresses`).
- Nobody reads `is_connected` outside the handler.

```python
# pyagora.ap
class AgoraAPClient:
    def __init__(self, session: aiohttp.ClientSession | None = None, *, verify_ssl: bool = True) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc) -> None: ...
    async def choose_server(self, creds: ChannelCredentials, *, role: int = 1,
                            service_flags: Sequence[int] = (11, 26), sid: str | None = None,
                            proxy_server: str | None = None) -> APResponse: ...
    async def update_ticket(self, creds: ChannelCredentials, edges: Sequence[EdgeAddress], *, sid: str | None = None,
                            service_flags: Sequence[int] = (11,), proxy_server: str | None = None) -> APResponse: ...

class APResponse:  # today's AgoraResponse
    def get_gateway_addresses(self) -> list[EdgeAddress]: ...
    def get_turn_addresses(self) -> list[EdgeAddress]: ...
    def get_ice_servers(self, *, use_all_turn_servers: bool = False, turn_mode: TurnMode = TurnMode.ALL,
                        credentials: TurnCredentialStrategy = ...) -> list[ICEServer]: ...
    def to_ap_response(self, flag: int | None = None) -> dict[str, Any]: ...
    def turn_server_config(self, gateway: EdgeAddress | None, token: str | None) -> dict[str, Any]: ...

# pyagora.session
@dataclass(frozen=True, kw_only=True)
class SessionOptions:
    client_codec: str = "vp8"; target_uid: int | None = None
    send_set_client_role: bool = False; instant_video: bool = False
    declare_remote_video_ssrc: bool = False; disable_audio: bool = False
    subscribe_retry: tuple[int, float] = (0, 0.0); strip_mid_extension: bool = True
    join_timeout: float = 15.0; connect_timeout: float = 10.0

class AgoraSession:
    def __init__(self, creds: ChannelCredentials, ap: APResponse, options: SessionOptions = SessionOptions(), *,
                 token_provider: Callable[[], Awaitable[str | None]] | None = None,
                 on_peer_left: Callable[[int], Awaitable[None]] | None = None,      # host implements recovery policy
                 on_session_ended: Callable[[EndReason], Awaitable[None]] | None = None,  # quit(2003) / p2p_lost / ws closed
                 keepalive: Callable[[], Awaitable[bool]] | None = None, keepalive_interval: float = 3.0,
                 deadline: float | None = None) -> None: ...
    def add_ice_candidate(self, candidate: str | IceCandidate) -> None: ...   # before join
    async def join(self, offer_sdp: str, session_id: str) -> str: ...        # answer SDP; raises on failure (no fake SDP)
    async def renew_token(self, token: str | None = None) -> None: ...
    async def close(self) -> None: ...                                         # sends leave, cancels + awaits tasks
    @property
    def is_connected(self) -> bool: ...
    @property
    def remote_users(self) -> frozenset[int]: ...

# pyagora.rtm
class RtmRestClient:
    def __init__(self, creds: RtmCredentials, session: aiohttp.ClientSession | None = None) -> None: ...
    async def send_peer_message(self, payload: Mapping[str, Any], *, wait_for_ack: bool,
                                accept: frozenset[str] = ...) -> bool: ...
    def update_token(self, token: str) -> None: ...
    async def close(self) -> None: ...

# pyagora.sdp (pure)
def offer_to_ortc(offer_sdp: str) -> dict[str, Any]: ...
def answer_from_ortc(ortc: dict[str, Any], offer_sdp: str, *, strip_mid: bool = True,
                     remote_video: RemoteStream | None = None, disable_audio: bool = False) -> str: ...
def candidates_to_ortc(cands: Iterable[str]) -> list[dict[str, Any]]: ...
def parse_trickle_fragment(fragment: str) -> list[IceCandidate]: ...
def filter_candidates(cands, turn_ips) -> list[IceCandidate]: ...
```

Mapping from today's calls:
- B `connect_and_join(data, offer, sid, ap)` → `AgoraSession(creds, ap, …).join(offer, sid)`. The same applies to C, with `app_id` moving into the credentials.
- `handler.candidates = [...]` → `add_ice_candidate`. B's reset at `camera.py:267` becomes a fresh session per offer.
- The FPV `availableTime` becomes `deadline=`.

---

## 7. Third-party dependencies

| Dep | Used by | Needed? |
|---|---|---|
| `aiohttp` | AP client (FormData POST); RTM REST; dead `_get_agora_edge_services` (`Bws:1874-1894`, MultipartWriter) | **Yes** (AP, RTM). Only the `ClientSession` API is used. |
| `websockets` | `websockets.asyncio.client.connect/ClientConnection`, `WebSocketException` (`Bws:23-24`, `Cws:19-20`) | **Yes.** The asyncio client exists since **13.0**, and nothing 15/16-specific is used. PyAgora pins `>=16.0`, HA-Luba `>=15.0.1`, PetKit `==15.0.1` (its manifest). A floor of `>=13` (or `>=14`) avoids fighting HA's pin. |
| `sdp-transform` | `_parse_offer_sdp` in B/C; `whep_proxy._parse_trickle_candidates` | **Redundant with `agora_sdp.SDPParser`.** B and C both parse every offer twice with two different parsers, and the hand parser has the direction bug (B) or the rtcp-fb gap (C). Keep one. sdp-transform is pure Python and small, and parses direction, candidates and rtcp-fb correctly. |
| `webrtc_models` | `RTCIceCandidateInit` (handler), `RTCIceServer` (hosts) | **Not needed** by the library: replace with its own dataclass. It is not declared in PyAgora's `pyproject.toml` although A imports it. |
| `homeassistant` | A/B websocket type import | **No** (only `async_create_task`) |
| `ruff` | listed as a *runtime* dependency in `PyAgora/pyproject.toml` | Move it to dev |
| `go2rtc_client`, `pypetkitapi`, `pymammotion` | hosts only | No |

---

## 8. Test seams and fixture material

**Pure, directly unit-testable:**
- `derive_password` (`Bapi:39-55`)
- `AgoraResponse.from_api_response` / `get_*_addresses` / `get_ice_servers` / `to_ap_response` / `get_turn_server_config`
- `AgoraAPIClient._build_request_payload` and `merge_objects` (inject `time`/rng)
- `SDPParser.parse`, `parse_offer_to_ortc`
- The answer builder: currently a method, but it uses only `ortc`, `SdpInfo` and three flags (`Bws:1424-1688`, `Cws:1086-1134`). Lift it to `sdp.answer_from_ortc`.
- `_negotiated_caps`, `_convert_candidates_to_ortc`, `_find_existing_video_streams`, `_extract_existing_video_stream`, `_validate_sdp`, `_create_join_message` (inject clock/ids)
- C `_filter_candidates`, `_parse_trickle_candidates`
- RTM `_iter_endpoints`, `_extract_rtm_credentials`

**Session fakes:**
- Inject a `connect` factory, or run an in-process `websockets.asyncio.server` on loopback. Existing HA-Luba tests already fake the socket by setting `handler._websocket = AsyncMock()` and decoding `send.await_args_list` (`tests_ha/test_agora_renew_token_debounce.py:44-57`).
- Freeze `time.monotonic` rather than monkeypatching the module's `time`/`asyncio`, as `test_fpv_keepalive.py:61,113` and `test_agora_peer_recovery_cap.py:36-40` do today.
- Port these HA-Luba tests as library tests with the `hass` fixture deleted: `test_agora_answer_sdp.py`, `…camera_uid_filter.py`, `…peer_recovery_cap.py`, `…renew_token_debounce.py`, `…session_quit.py`, `test_fpv_keepalive.py`.

**HTTP fakes:** `aioresponses` or an `aiohttp.web` loopback app serving `/api/v2/transpond/webrtc` (multipart field `request`) and the RTM `peer_messages` path.

**Message shapes found in code, comments and tests (fixture seeds):**
- AP response: `{"enter_ts", "opid", "detail":{}, "response_body":[{"uri", "buffer":{"flag":4096|4194310, "code":0, "cert", "uid", "cid", "cname", "detail":{"19":"sha-256 AA:…;…", "8":…, "4":…}, "edges_services":[{"ip","port"}]}}]}`. Sources: `HA-Luba/AGORA_COMPLETE_FLOW.md:55-90`, `HA-Luba/test_credential_fallback.py:31-55`. The JS decoder adds `fingerprint`, `iceUfrag`, `icePwd` per edge (`js:~43465-43475`).
- AP request: `Bapi:761-772` / `Capi:378-395` (`appid, client_ts, opid, sid, request_bodies[{uri:22|28, buffer:{cname, detail{11,17,22[,6]}, key, service_ids, uid[, edges_services]}}]`).
- `join_v3`: `Bws:971-1019`, `Cws:662-713`; the JS reference shape is at `js:46225-46363`.
- Join success: `{"_result":"success", "_message":{"ortc":{iceParameters{iceUfrag, icePwd, candidates[{foundation, ip, port, priority, protocol, type}]}, dtlsParameters{role, fingerprints[{algorithm, fingerprint}]}, rtpCapabilities{sendrecv|recv|send:{audioCodecs, videoCodecs[{payloadType, rtpMap{encodingName, clockRate}, rtcpFeedbacks, fmtp{parameters}}], …Extensions}}, cname}, "rejoin_token", "cid", "uid", "vid", "cname"}}`. The ready-made ORTC is at `tests_ha/test_agora_answer_sdp.py:25-65`.
- Unsolicited messages:
  - `on_add_video_stream {uid, ssrcId, rtxSsrcId, cname, codec, pt, video}`
  - `on_user_online/offline {uid, reason}`
  - `on_notification {action:"quit", code:2003, detail:"ERR_REPEAT_JOIN"}` (`tests_ha/test_agora_session_quit.py:12-13`)
  - `on_p2p_ok {uid, proxy}`, `on_p2p_lost {error_code, error_str}`
  - `on_rtp_capability_change {video_codec, extmap_allow_mixed, web_av1_svc}`
  - `on_token_privilege_will_expire` / `…did_expire`, `error {error}`, `answer {sdp}`
- Outbound frames:
  - `ping {_id, _type}`, `leave`
  - `renew_token {token}`
  - `subscribe {stream_id, stream_type, mode, codec, p2p_id, twcc, rtx, extend, ssrcId}`
  - `unsubscribe {p2p_id, ortc:[], stream_id}`
  - `set_client_role {role, level, client_ts}`
- RTM: request and headers at `agora_rtm.py:208-225`; response `{"result":"success","code":"message_delivered"}`.

**Offer fixtures still needed** (none are checked in):
- a Chrome recvonly offer with trickle;
- a go2rtc/pion non-trickle offer with inline candidates;
- an offer carrying `extmap …/recvonly` and H265 `profile-id=1`.

The probe offer in §1.2 is a minimal seed, and it reproduces both the B direction bug and the C rtcp-fb regression.
