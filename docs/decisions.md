# Decisions

Numbered, append-only once released. Until the first release an entry may
be amended in place; after that a decision is superseded by a later entry,
never edited away. Where a decision resolves a conflict between the three prior
copies of this code (PyAgoraRTC snapshot, HA-Luba/Mammotion, PetKit), the
analysis behind it is `docs/analysis/divergence.md`.

## D1. HA-Luba is the behavioural base; PetKit fixes are ported; its regressions are not

The Mammotion copy carries the most fixes and the only working session-level
recovery. From PetKit we take: no fabricated fallback SDP on join failure,
verified TLS on the gateway socket, browser candidates actually sent in the
join ORTC, subscribing to streams already present in the join payload, the
subscribe dedupe and retry, answer fallback to the offer's payload types when
the gateway lists no codec, awaited task cancellation, `CancelledError`
re-raised, skipping a failed AP buffer instead of failing discovery, and
`attributes.userAttributes` nesting. We do not take: dropping `a=rtcp-fb`
from the ORTC, dropping `can_send`, calling `set_client_role` after join,
omitting `leave`, not clearing `_online_users`, or the hard-coded `a=setup`.

## D2. One credential model, vendor-agnostic

`ChannelCredentials(app_id, channel_name, token, uid, string_uid, area_code,
license, encryption)` plus `RtmCredentials`. Hosts map their vendor model to
it. Token refresh is a `token_provider` callback, so the dataclass stays
frozen. `availableTime`, per-camera tokens and camera slots are host
concerns: the first becomes `deadline=`, the others `target_uid`.

## D3. One SDP parser: `sdp_transform`

The hand-written parser never captured `a=recvonly`/`sendonly` (its branch
could not match), and PetKit's rewrite dropped `a=rtcp-fb`. Every copy also
parsed each offer twice. `sdp_transform` parses direction, feedback,
candidates and the RFC 5285 extmap direction suffix correctly. `offer_to_ortc`
and `answer_from_ortc` are pure functions over its output, and a parity
fixture pins their results against the previously shipped ORTC for the same
offer.

## D4. The ORTC sent to Agora declares DTLS role `server` by default

The JS SDK sends no role; HA-Luba sends `server` and that resolved a DTLS
deadlock in practice; PetKit sends `client` and works, apparently because the
gateway ignores it. Default `server` (proven), exposed as
`SessionOptions.ortc_dtls_role` with `None` meaning "send none" for parity
experiments. Q2 tracks it.

## D5. The answer's `a=setup` mirrors the gateway's DTLS role

`server` → `passive`, `client` → `active`, exactly as the JS SDK does.
Hard-coding `active` (PetKit, old snapshot) only works while the gateway
happens to take the client role. `auto` → `active`, not the SDK's `actpass`:
RFC 5763 §5 forbids `actpass` in an answer, and `active` is valid against
either gateway role.

## D6. `set_client_role` after join is off by default

It made Mammotion mowers leave the channel about half a second after video
started. Joining with `role: host` is sufficient. PetKit's copy sends it;
whether PetKit devices need it is Q3, so it is `SessionOptions.
send_set_client_role`, default `False`.

## D7. The join frame follows the JS SDK's shape

`attributes: {userAttributes: {...}}` nested (the gateway tolerated the flat
form, but the SDK is the contract), `enablePreallocPC: true`, and, when the
credentials carry them, `license` and `string_uid` / `details.6`. The
`aes_*` fields are never sent (D20). The codec (`vp8` for Mammotion, `h264` for
PetKit) is `SessionOptions.client_codec` and is also used for `subscribe`.

## D8. Token renewal asks the host first, then reuses the join token

`on_token_privilege_will_expire` arrives about once a second. Renewal is
debounced (30 s), calls `token_provider` when one is given (the SDK's
behaviour: the app supplies a new token), and otherwise resends the last
token it sent (initially the join token; the Mammotion behaviour, which
the gateway accepts). The Mammotion copy also renewed when a peer left;
that step is dropped as it traced to nothing.

## D9. A failed join raises; it never returns a made-up answer

The old fallback SDP with random ICE credentials could not connect and hid
the failure from the viewer. `join()` raises `JoinRejectedError`,
`JoinTimeoutError` or `GatewayConnectError`.

## D10. TLS is verified on both the AP call and the gateway socket

PetKit's copy proved the edges present valid certificates. `verify_ssl=False`
remains available on both clients for captive or proxied networks.

## D11. Candidates known before join go into the join ORTC; nothing is trickled after

That is what the JS SDK does and what PetKit's copy does. HA-Luba collected
candidates and never sent them; the gateway is ice-lite and its own
candidates are in the answer, so viewers connect regardless. Post-join
trickle (`whep_proxy` PATCH) is accepted by the host but there is no gateway
message to carry it (Q4).

## D12. TURN credentials: uid-derived by default, AP-detail optional

HA-Luba ignores the AP response's detail `8`/`4` credentials because they
produced TURN 401s and derives username/password from the uid. PetKit tries
the detail credentials first, then the uid, then `test`/`111111`.
`TurnCredentialStrategy.UID` is the default; `DETAIL_FIRST` is available.
Q5 asks whether the detail credentials ever work.

## D13. Background work is owned, and spawning is injectable

The session keeps a `set[asyncio.Task]`, spawns through one `_spawn`, and
`close()` cancels and awaits them all (except the current task). `spawn=`
is a constructor argument so a host may pass its own task factory (Home
Assistant's background-task helper) and get the same lifetime guarantees.
Every timer waits through the injected `sleep` (default `asyncio.sleep`),
which is what lets tests drive time with a manual clock.

## D14. One `on_closed(CloseReason)` and one `on_peer_left(uid)`

The four ways a session ends (gateway `quit` notification, `p2p_lost`,
socket closed, keep-alive deadline) fire one callback once with a reason
enum. A peer leaving fires `on_peer_left` only after `PeerRecovery`'s
debounce, cooldown and attempt cap have passed; the host decides what
recovery means. This replaces `session_ended`, `on_connection_lost` and
`recover_stream`.

## D15. Keep-alive is a generic periodic callback with a deadline

`keepalive: Callable[[], Awaitable[bool]]` runs every `keepalive_interval_s`
seconds (3 s, the shipped cadence) while joined; returning `False` stops it.
`deadline` (absolute monotonic seconds) closes the session with
`CloseReason.DEADLINE`. Mammotion's 4G FPV `refresh_fpv` and its
`availableTime` budget are the host's use of these.

## D16. The MID header extension is stripped from the answer only

That is what shipped and works. The memory note that it was also stripped
from the ORTC was never true; whether stripping it there too is better is
Q7.

## D17. `msid` is not written unless a remote stream is declared

No shipped copy wrote `a=msid` for the plain case; the "stream id `1`" fix
in the memory note never landed. With `declare_remote_video_ssrc` the answer
carries `a=ssrc`, `a=ssrc-group:FID` and `a=msid` (PetKit's go2rtc need).
Q8 tracks whether a plain `msid` helps browsers.

## D18. RTM: the REST transport is in the library, the vocabulary is not

`RtmRestClient` sends `peer_messages` with endpoint rotation, the three
Agora auth headers and the ack semantics. `start_live`, `live_heartbeat`,
`stop_live`, `ptz_ctrl` are PetKit's protocol and stay with the host.

## D19. Dependencies: aiohttp, websockets ≥ 13, sdp-transform; nothing else

`webrtc_models` is replaced by the library's own `IceCandidate` and
`ICEServer` (hosts convert). `homeassistant` was only `async_create_task`.
`websockets` uses the asyncio client API that has existed since 13.0, so the
floor is 13.1 to avoid fighting a host's pin.

## D20. Encryption is modelled, not sent

`ChannelEncryption` stays on `ChannelCredentials`, but `build_join` sends no
`aes_*` field and logs one WARNING when it is set. The SDK does not send the
secret as given: it RSA-OAEP-SHA256-wraps it with a public key embedded in
the SDK, base64-encodes the result and adds `aes_encrypt: true` (Q17).
Sending the raw secret would be a malformed join. Implementing the wrap
needs a crypto dependency (D19 allows none), and it would buy nothing: the
media stays encrypted and no consumer this library serves can decrypt it
(architecture §6). Q6 asks whether any device turns it on.

## D21. Tests mirror the package; fakes live in one file per tier

`tests/unit/<pkg>/test_<module>.py`; `tests/unit/_fakes.py` holds the
gateway-transport and HTTP fakes; `tests/fakegateway/` is an in-process
websockets server plus aiohttp AP/RTM fake that speaks the recorded
protocol; `tests/fixtures/` holds redacted recordings that unit and
integration tests both load.

## D22. `on_p2p_lost` ends the session only when asked

The Mammotion integration registered no handler for `on_p2p_lost` on
purpose (its restart path was disabled), while PetKit ends the session on
it. `SessionOptions.end_on_p2p_lost` defaults to `False` (the frame is
logged), PetKit sets `True`. This narrows D14: `CloseReason.P2P_LOST` is
reachable only with the option on. Q13 asks what the frame actually means
for a subscriber-only client.

## D23. `on_closed` fires exactly once for every ending, host close included

`close()` fires `on_closed(CLOSED_BY_HOST)`; a failed `join()` cleans up its
socket and tasks and fires `on_closed(JOIN_FAILED)` before raising (a socket
that closes after the join result but before `join()` acts on it is a failed
join, `GatewayConnectError`, not a silently dead joined session); the
gateway's quit, a socket close, the deadline and (with D22) `p2p_lost` fire
their reasons. `close()` is idempotent and safe after a failed join. A
candidate added after `join()` is ignored with a DEBUG line (Q4).

## D24. `parse_offer` restores what `sdp_transform` coerces (amends D3)

`sdp_transform` turns any numeric-looking value into a number: an
`ice-ufrag` of `0012` becomes `12`, an `ice-pwd` of `1e10` a float, and the
same happens to `mid`, payload lists, `group` mids, `a=fmtp` configs and
`a=ssrc` values (`0012`, `nan`, `inf`). `parse_offer` walks the raw lines
with sdp_transform's own grammar and restores those attributes verbatim. Candidates never pass
through `sdp_transform`; they stay verbatim `candidate:` strings split by
grammar position, as both shipped copies did.

## D25. Answer rules that deviate from the shipped builder

When the gateway lists no codec for an m-line the answer reuses the offer's
payload types **with** their `rtpmap`/`fmtp` lines (PetKit emitted bare
dynamic PTs, which is invalid): only a PT that carries an `a=rtpmap` or is
statically assigned (RFC 3551, 0-34) is kept, and a section left with none
is rejected with port 0; an offer m-line with no direction is the RFC
default `sendrecv`; BUNDLE lists the offer's mids rather than a hard-coded
`0 1`, and an m-line the offer gave no `a=mid` gets none and stays out of
BUNDLE; a non-audio/video m-line (a data channel) is rejected with port 0
and left out of BUNDLE; a missing ICE credential or fingerprint in the
gateway ORTC is an `SdpError`, never a random stand-in (D9).

## D26. The AP's detail-19 fingerprint fills in a gateway ORTC that has none

Both shipped hosts merged the access point's per-edge DTLS fingerprint
(detail 19, matched to edges by index) into the join response's
`dtlsParameters.fingerprints`. The answer uses only the first fingerprint,
and the gateway's own comes first, so merging changed the answer only when
the gateway sent none. `AgoraSession._join` therefore fills the list only
then: from the edge the socket connected to, else the first gateway edge
that has one, never both; with neither, `answer_from_ortc` raises `SdpError`
as before (D9). Parsing lives beside the parser of detail 19,
`ap/response.py::fingerprints_from_edge`: `algorithm value` keeps its
algorithm, a bare value reads as `sha-256`, anything else is ignored. Q19
asks whether the gateway ever omits its fingerprint.

## D27. websockets' frame log is kept off

websockets logs every frame at DEBUG, truncated to its first and last few
dozen characters, so a `renew_token` frame is logged whole, token included
(Constitution §6). `WebsocketsTransport` passes it the logger
`pyagorartc.session.wire`, set to INFO. A host that needs the frames can set
that logger to DEBUG on purpose; the session's own frame lines go through
`describe_frame` and carry no values.
