# Fixtures

Provenance for every file here, using the labels in `docs/protocol.md`
("Provenance labels"). Addresses are loopback or RFC 5737 documentation
ranges; ICE passwords and tokens are placeholders (`docs/protocol.md`,
"Redaction rules applied").

## sdp/

| File | Label | Source and edits |
|---|---|---|
| `chrome_recvonly_offer.sdp` | CAPTURED, then extended | `docs/protocol.md` §6.1 (HA frontend on Chrome). Added to exercise the parser: one trickle host candidate in the audio section (`192.0.2.50`), `a=extmap:12/recvonly …abs-capture-time` (RFC 8285 direction suffix, Q1), and an H265 `profile-id=1` codec (PT 49, already in the m-line) with `a=rtcp-fb:49 nack pli`. Everything else is byte-for-byte §6.1. |
| `go2rtc_offer.sdp` | RECONSTRUCTED | A pion/go2rtc-style non-trickle offer: video first (`mid:0`), audio second, session-level fingerprint, inline host and srflx candidates for components 1 and 2, `a=end-of-candidates`. Shaped from pion's SDP writer; no capture exists yet (`docs/backlog.md`). |
| `gateway_join_ortc.json` | CODE (trimmed real response) | The `ortc` object of the `join_v3` success response, `docs/protocol.md` §2.5 / `HA-Luba/tests_ha/test_agora_answer_sdp.py`. Candidate address redacted to `198.51.100.13`. |
| `gateway_ortc_vp8.json` | CODE | §2.5 with the opus + VP8 capability set and the extension lists `docs/protocol.md` §6.3 describes (audio {1 ssrc-audio-level, 4 mid}, video {2 abs-send-time, 3 twcc, 4 mid}). |
| `chrome_offer_ortc_shipped.json` | GENERATED | HA-Luba's shipped `agora_sdp.parse_offer_to_ortc` run on `chrome_recvonly_offer.sdp`. The D3 parity fixture for `pyagorartc.sdp.offer_to_ortc`. |
| `chrome_answer_shipped.sdp` | GENERATED | HA-Luba's shipped `AgoraWebSocketHandler._generate_answer_sdp` run on `chrome_recvonly_offer.sdp` and `gateway_ortc_vp8.json`. Identical to `docs/protocol.md` §6.3 apart from the redacted address. The parity fixture for `pyagorartc.sdp.answer_from_ortc`. |
| `whep_trickle_fragment.sdpfrag` | RECONSTRUCTED | A WHEP `PATCH` body in the RFC 8840 `application/trickle-ice-sdpfrag` shape, as `petkit/whep_proxy.py` receives it: one host, srflx, prflx and two relay candidates (one on a TURN address `198.51.100.30`, one elsewhere) for `mid:0`, `a=end-of-candidates`, then a TCP host candidate for `mid:1`. |

To regenerate the two GENERATED files, run the shipped HA-Luba functions on the
inputs above with CRLF line endings (as a browser delivers them) and write the
answer back with LF.

## gateway/

No server-to-client gateway frame has been captured (`docs/protocol.md`
opening note; `docs/backlog.md`). Every inbound file below is RECONSTRUCTED
from the section cited: the fields our handlers read, the SDK's parser, and
illustrative values. The outbound `*_expected.json` files are CODE: the exact
frames the shipped HA-Luba builders sent, with the D7/D20 changes applied.
Ids, timestamps and credentials are the fixed test values in
`join_v3_inputs.json` and `tests/_helpers.py`.

| File | Label | Source and edits |
|---|---|---|
| `join_ok.json` | RECONSTRUCTED | §2.5 success response (ORTC from the trimmed real response; candidate `192.0.2.13`), plus a `rejoin_token` and a `streams` list holding one video stream and one audio-only entry. The key the server uses for already-published streams is unverified (§2.5 last bullet); PetKit's walk finds them under any key. |
| `join_ok_rtx.json` | RECONSTRUCTED | `join_ok.json`'s ORTC with an `rtx` video codec (the live gateway lists none, §2.5), no `rejoin_token`, no streams. |
| `join_ok_no_ortc.json` | RECONSTRUCTED | A success response without `ortc`. |
| `join_failed.json` | SDK | §5 failed-request shape (`error_code`, `error_str`). |
| `join_failed_code_only.json` | SDK | The SDK's `error_code \|\| code` fallback, with the code as a string (`Number()` coerces it). |
| `on_add_video_stream.json` | RECONSTRUCTED | §3.2 with a real payload type (102, H264). |
| `on_add_video_stream_h265_pt0.json` | RECONSTRUCTED | §3.2 verbatim: H265 publisher, `pt: 0`. |
| `on_add_video_stream_minimal.json` | SDK | The rejoin replay variant `{uid, uint_id, video, ssrcId}` (§3.2). |
| `on_add_video_stream_no_ssrc.json` | RECONSTRUCTED | Missing `ssrcId`: the tolerance case. |
| `on_user_online.json`, `on_user_offline.json` | RECONSTRUCTED | §3.2; `reason` values are unverified. |
| `on_user_offline_no_uid.json` | RECONSTRUCTED | Missing `uid`: the tolerance case. |
| `on_notification_quit.json`, `on_notification_warn.json` | CAPTURED shape | §5, from `tests_ha/test_agora_session_quit.py`. |
| `on_p2p_lost.json` | SDK | §5, fields in `_message` as the §2.1 envelope implies. |
| `on_p2p_lost_top_level_only.json` | RECONSTRUCTED | Fields only at the top level, HA-Luba's reading; pins the fallback. |
| `on_p2p_lost_top_level.json` | RECONSTRUCTED | Fields in both places with different values, the placement HA-Luba's handler read (§5); pins that `_message` wins. |
| `on_p2p_ok.json`, `on_rtp_capability_change.json` | RECONSTRUCTED | §3.2. |
| `on_rtp_capability_change_irregular.json` | RECONSTRUCTED | Non-string codec entries and non-boolean flags: the tolerance case. |
| `on_token_privilege_will_expire.json`, `on_token_privilege_did_expire.json` | RECONSTRUCTED | §4; payloads unverified and unread. |
| `error.json` | CODE | §5 `error` event. |
| `ping_back.json` | RECONSTRUCTED | §4: the gateway's `_id`-correlated reply to `ping`; its `_message` contents are unverified. |
| `unknown_event.json` | RECONSTRUCTED | An SDK event the library does not handle (`on_published_user_list`) with an extra top-level key. |
| `non_object_message.json` | RECONSTRUCTED | An event whose `_message` is not an object. |
| `join_v3_inputs.json` | CODE | The inputs `join_v3_expected.json` is built from: fixed `_id`, session id, process id, `join_ts`, a small client ORTC and the flag-4096 AP block. |
| `join_v3_expected.json` | CODE | §2.3 with D7 applied: flags nested under `attributes.userAttributes`. The `browser` value writes the last dot of the four-part Chrome version as the JSON escape `\u002e`, so the fixture address check does not read the version as an IPv4 address; it decodes to the shipped string. |
| `subscribe_expected.json`, `unsubscribe_expected.json`, `set_client_role_expected.json` | CODE | §3.3. |
| `ping_expected.json`, `leave_expected.json`, `renew_token_expected.json` | CODE | §4. |
