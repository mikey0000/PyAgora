# Architecture

`pyagorartc` drives an Agora RTC session from Python so that a WebRTC consumer
that is not the Agora SDK (a browser, go2rtc, pion) can receive a stream a
device publishes to an Agora channel. It does three things: find the edge
(`ap/`), translate between the consumer's SDP and Agora's ORTC (`sdp/`), and
run the gateway WebSocket session that joins, subscribes and keeps the stream
alive (`session/`). A fourth, independent piece sends Agora RTM peer messages
over REST (`rtm/`), which some vendors use as their device control channel.

This is the map. The rules are in `CONSTITUTION.md`; the reasons in
`decisions.md`; what the wire looks like in `protocol.md`.

## 1. Layer map

```
┌───────────────────────────────────────────────────────────────┐
│ host (Home Assistant integration, script, …)                  │
│   owns: vendor API, credentials fetch, viewer, recovery policy│
├───────────────────────────────────────────────────────────────┤
│ session/                                                       │
│   session.py    AgoraSession: connect → join → answer SDP →   │
│                 subscribe → keep alive → close                 │
│   messages.py   pure builders/parsers for every gateway frame  │
│   recovery.py   PeerRecovery, Keepalive, RenewDebounce and     │
│                 PingWatchdog policies (pure; the runtime owns  │
│                 the loops)                                     │
│   transport.py  GatewayTransport protocol, websockets adapter, │
│                 ssl_for(url)                                   │
├───────────────────────────────────────────────────────────────┤
│ rtm/                                                           │
│   client.py     RtmRestClient: peer_messages over REST,        │
│                 endpoint rotation, ack semantics               │
├───────────────────────────────────────────────────────────────┤
│ ap/                                                            │
│   client.py     AgoraAPClient: choose_server, update_ticket    │
│   response.py   APResponse: edges, TURN, ICE servers, strategy │
│   password.py   derive_password (uid → TURN credential)        │
├───────────────────────────────────────────────────────────────┤
│ sdp/  (pure: no I/O, no clock)                                 │
│   offer.py      offer_to_ortc: SDP offer → ORTC capabilities   │
│   answer.py     answer_from_ortc: gateway ORTC → answer SDP    │
│   candidates.py candidates_to_ortc, extract_inline_candidates, │
│                 parse_trickle_fragment, filter_candidates      │
├───────────────────────────────────────────────────────────────┤
│ models.py  ChannelCredentials, ChannelEncryption,              │
│            RtmCredentials, IceCandidate, ICEServer,            │
│            EdgeAddress, TurnMode, CloseReason, RemoteStream,   │
│            SessionOptions, as_int (the wire-integer rule)      │
│ exceptions.py  the whole hierarchy                             │
│ const.py       hosts, SDK version string, service ids, timers  │
└───────────────────────────────────────────────────────────────┘
```

Imports go downward only: `session/` may import `sdp/`, `ap/`, `models`,
`exceptions`, `const`; `ap/` and `rtm/` import only `models`, `exceptions`,
`const`; `sdp/` imports only `models`, `exceptions` and `const` (values, never
I/O). Nothing under
`pyagorartc/` imports `homeassistant`, `webrtc_models`, `pymammotion`,
`pypetkitapi`, `go2rtc_client`, or any other host or vendor package.
`tests/meta/test_conventions.py` asserts this.

## 2. The session lifecycle

```
host fetches vendor credentials → ChannelCredentials
host: ap = await AgoraAPClient(session).choose_server(creds)      (HTTP)
host: ice = ap.get_ice_servers(...)                               → viewer
viewer produces offer SDP (+ optional inline/trickle candidates)
host: s = AgoraSession(creds, ap, options, callbacks…)
      s.add_ice_candidate(c) for each candidate known before join
      answer = await s.join(offer_sdp, session_id)
        1. sdp.offer_to_ortc(offer); iceParameters.candidates =
           candidates_to_ortc(extract_inline_candidates(offer) + added)
        2. transport.connect(gateway edge, verified TLS); start the
           message loop (it resolves responses by _id)
        3. send join_v3 (messages.build_join); wait for the join result
           (JoinRejectedError / JoinTimeoutError; never a fabricated SDP)
        4. subscribe to streams already in the join payload, and to each
           announced stream once both on_add_video_stream and
           on_user_online for its publisher have been seen (any order;
           a held stream logs its uid at DEBUG, Q18) — or on the
           announcement alone with subscribe_requires_online=False (D28),
           filtered by target_uid and never our own uid; each subscribe
           task calls on_stream(stream) once, after its first subscribe
        5. start owned tasks: ping, keepalive (if given); every timer
           waits through the injected `sleep` so tests drive time
        6. with declare_remote_video_ssrc, wait (≤ 15 s) for a stream
        7. sdp.answer_from_ortc(gateway ortc, offer, options), the ortc's
           missing fingerprint filled from the connected edge's AP detail 19 (D26)
           (setup mirrors the gateway DTLS role; MID extension stripped;
            optional declared remote SSRC; optional audio inactive)
      viewer applies answer; media flows browser ⇄ Agora edge (SRTP)
running: renew_token (debounced, token_provider or the last token sent),
         on_user_offline → cancel its subscribe tasks, one unsubscribe
           → PeerRecovery → host's on_peer_left,
         ping and subscribe replies resolve by _id (D31); ten ping ticks
           without a reply and 10 s without any frame → PING_TIMEOUT,
         on_notification quit / on_p2p_lost / ws closed (or the message
           loop stopping) / deadline → _end → on_closed(CloseReason) once
         state: is_connected, is_joined, remote_users, remote_streams
host: await s.close()   → _end(CLOSED_BY_HOST): cancel and await every
                          owned task, leave (bounded), close the socket
```

Every ending runs through `_end`, which cancels the owned tasks; once it
has started, `_spawn` refuses new ones, so nothing outlives the session.

A session is single-use: one offer, one join, one close. A new offer is a
new `AgoraSession`. That is what keeps state simple enough to reason about.

## 3. Single homes

| Question | The one place that answers it |
|---|---|
| What does the join frame contain? | `session/messages.py::build_join` |
| What does a subscribe frame contain? | `session/messages.py::build_subscribe` |
| How is an offer turned into ORTC? | `sdp/offer.py::offer_to_ortc` (one parser: `sdp_transform`) |
| Which codecs may the browser send? | `sdp/offer.py::can_send` |
| How is the answer built? | `sdp/answer.py::answer_from_ortc` |
| Where does the answer's fingerprint come from when the gateway sends none? | `ap/response.py::fingerprints_from_edge` (detail 19 of the connected edge), applied once in `session/session.py::_join` (D26) |
| Which `a=setup` does the answer carry? | `sdp/answer.py::setup_for_role` (mirrors gateway DTLS role) |
| Which header extensions are dropped? | `sdp/answer.py::STRIPPED_EXTENSIONS` (MID) |
| How are candidates encoded for the join? | `sdp/candidates.py::candidates_to_ortc` (the session feeds it `extract_inline_candidates(offer)` plus host-added ones) |
| Is the subscribe `rtx` flag set? | `sdp/answer.py::offers_rtx` (`JoinResult.offers_rtx` delegates to it) |
| Where is the gateway's capability block? | `sdp/offer.py::negotiated_caps` (first non-empty bucket wins) |
| How is a wire integer read? | `models.py::as_int` (AP response and gateway frames alike) |
| How is a gateway frame logged? | `session/messages.py::describe_frame` (type, id, keys; never values); websockets' own frame log stays off (`session/transport.py`, D27) |
| Which candidates reach a viewer via TURN? | `sdp/candidates.py::filter_candidates` |
| Which TURN credential is used? | `ap/response.py::APResponse.get_ice_servers` (uid-derived, as the SDK; `DETAIL_FIRST` is deprecated, D32) |
| How is the uid-derived password made? | `ap/password.py::derive_password` |
| Which AP block is primary? | `ap/response.py::APResponse.from_api_response` (flag 4096 first; every scalar from the same block) |
| What does an AP `code` mean? | `ap/response.py::AP_RESPONSE_CODE_NAMES` / `describe_ap_code` (the SDK's `PV` names; used only in `APRejectedError`'s message) |
| Is this gateway frame something we handle? | `session/session.py::_HANDLERS` table |
| When does a peer's absence trigger recovery? | `session/recovery.py::PeerRecovery` |
| When is a silent gateway given up on? | `session/recovery.py::PingWatchdog` (D31), driven by `session/session.py::_ping_loop` |
| When does the session end, and who is told? | `session/session.py::_end(reason)` → `on_closed` once |
| Where do background tasks live? | `session/session.py::_spawn` (owned set; cancelled and awaited in `_end`, which `close` delegates to; refused once ended) |
| How is a secret printed? | `models.py` `__repr__` redaction; `fingerprint()` for logs |
| How is a live session turned into fixtures? | `capture.py` (`pyagorartc.capture` logger at DEBUG, redacted; D30) |
| Which RTM endpoint is tried next? | `rtm/client.py::RtmRestClient._iter_endpoints` |

## 4. What is vendor policy, and how it plugs in

| Concern | Library provides | Host provides |
|---|---|---|
| Token refresh | `renew_token`, debounce, `token_provider` callback | the vendor call that mints a token |
| Device keep-alive (Mammotion 4G FPV) | `keepalive` periodic callback, `keepalive_interval_s`, `deadline` | the MQTT/HTTP call; returns `False` to stop |
| Stream recovery after peer leaves | `PeerRecovery` timing (debounce, cooldown, attempt cap, reset) and `on_peer_left(uid)` | re-requesting the stream from the device |
| Which of several cameras | `SessionOptions.target_uid` | the mapping slot → uid |
| Knowing what is subscribed | `on_stream(stream)` once per subscription; `remote_users`, `remote_streams`, `is_connected`, `is_joined` | what to do with them |
| Consumer quirks (go2rtc/pion) | `declare_remote_video_ssrc`, `disable_audio`, `instant_video`, `subscribe_retry_attempts`, `subscribe_retry_delay_s`, `strip_mid_extension` (default on, D16) | choosing them |
| `set_client_role` after join | `SessionOptions.send_set_client_role` (default off) | knowing the device tolerates it |
| Join codec and flags | `client_codec` (default `vp8`, D7), `prealloc_pc` (default on, Q10), `extra_join_attributes` (merged into `userAttributes` last; stored read-only) | the codec its device publishes; any flag it needs |
| DTLS role in the client ORTC | `ortc_dtls_role` (default `server`; `None` sends none, D4/Q2) | a parity experiment |
| Subscribing before presence | `subscribe_requires_online` (default on, D28/Q18) | whether its gateway sends `on_user_online` for a publisher |
| Ending on `on_p2p_lost` | `end_on_p2p_lost` (default off, D22) | whether its device treats it as fatal |
| Renewal cadence | `renew_debounce_s` (default 30 s, D8/Q11) | nothing, unless the gateway penalises repeats |
| Timeouts | `join_timeout_s` (15 s), `connect_timeout_s` (10 s) | a slower network |
| Which gateway edge | `gateway_edge_offset` (default 0: the AP's first; wraps) | the camera slot, when cameras share one viewer uid (D33) |
| TLS on the gateway socket | `verify_ssl` (default on, D10) | a captive or proxied network |
| RTM control vocabulary (PetKit `start_live`…) | `RtmRestClient.send_peer_message` | the payloads and their cadence |
| Viewer transport (HA WebRTC, WHEP, go2rtc) | answer SDP, ICE servers, candidate helpers | the views, auth, relays |

## 5. Extension recipes

**A new gateway message** — add a parser/builder in `messages.py`, a handler
in the `_HANDLERS` table, a fixture under `tests/fixtures/gateway/`, a route
in `tests/fakegateway/`, tests in `tests/unit/session/test_messages.py` and
`test_session.py`, and a row in `protocol.md`.

**A new session option** — a field on `SessionOptions` with a default that
preserves today's behaviour, read in exactly one place, documented in
`architecture.md` §4 and `migration.md` if a host needs it.

**A new vendor** — no library change if the credentials fit
`ChannelCredentials` and the policies fit §4. If not, the new hook is a
decision first.

**A new AP service** — a service id in `const.py`, a builder branch in
`ap/client.py`, a fixture, tests.

## 6. What the library does not do

- It does not decrypt media. Agora channel encryption (`aes_*`) is applied
  by the SDK inside the media path; a plain WebRTC consumer cannot undo it.
  The fields are modelled but never sent (D20, Q17); an encrypted channel
  logs a WARNING at join.
- It does not relay media, run go2rtc, or serve HTTP. Those are host jobs.
- It does not decide recovery policy for a device; it times it.
- It does not know a vendor's REST API; credentials arrive already fetched.
