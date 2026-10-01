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

## Captured: `gateway/real/`, `ap/real/`, `sessions/`

CAPTURED 2026-10-01 from a Mammotion Luba 2 and Luba 3 via Home Assistant with
the `pyagorartc.capture` logger at DEBUG, redacted per D30, then normalised:

| Real value | Fixture value |
|---|---|
| both channel names (Mammotion iot ids) | `channel-test` |
| both viewer uids | `123456` |
| channel ids (AP `cid`, the gateway ufrag prefix) | `123456789` (Luba 2), `123456788` (Luba 3) |
| `vid` (join result, AP detail `8` of both blocks) | `987654` / `"987654"` |
| gateway edges and the gateway's host candidates | `203.0.113.10`+, in order of first appearance |
| the gateway's IPv6 host candidates | `2001:db8::5`, `2001:db8::b`, `2001:db8::c` (suffix kept) |
| TURN edges and the browser's relay candidates | `198.51.100.20`+ |
| the caller's public address (`wan_ip`, AP detail `1`, the browser's srflx) | `192.0.2.1` |
| AP top-level detail `502` (`csIp`, an Agora-side address) | `192.0.2.2` |
| AP detail `10` (an AccessToken2-shaped value the capture left in clear) | `DETAIL_10_REDACTED` |
| AP detail `3` (a country code) | `XX` |
| gateway `iceUfrag` (`<cid>_` + base64 that embeds the channel name) | `<cid>_ice-ufrag-gateway-test` |
| `<redacted>`: app id, token / `key` / `channel_key`, `cert` and `ticket`, detail `4`, `icePwd`, `rejoin_token`, `license` | `app-id-test`, `rtc-token-not-real`, `TICKET_REDACTED` (`TURN_TICKET_REDACTED` in the TURN block), `TURN_CRED_REDACTED`, `ice-pwd-gateway-test-001` (gateway) / `ice-pwd-offer-test-00001` (browser), `rejoin-token-not-real`, `license-not-real` |

Kept as recorded: timestamps, `_id`s, `opid`/`sid`, ports, ssrcs, the RTP
cname, DTLS fingerprints (public certificate hashes), HA session ids, process
ids and the browser's mDNS host names. The join's `browser` string writes the
last dot of the four-part Chrome version as `\u002e` so the address check does not read
it as IPv4. The generator is a scratch script, not in the repo.

`sessions/*.json` are one ordered list per scenario of
`{ts, source, direction, [session], [edge], frame}` (one entry per line), gateway and
AP exchanges together. The capture log does not say which socket a frame
travelled on, so where two viewers overlapped `session` is attributed from the
session logger (`Ignoring a stream …`, `Sent type=subscribe …`), `_id`
correlation and the 3 s ping phase; the two `leave`s of the racing pair are
attributed by join order and cannot be proved from the log.

| File | Content |
|---|---|
| `sessions/luba2_single.json` | Luba 2, one viewer targeting uid 2, 12:47:50–12:48:41, closed by the host. |
| `sessions/luba3_vision.json` | Luba 3 "Vision camera", target uid 1; uid 2 comes online and never publishes. Closed by the host. |
| `sessions/luba2_left_then_right.json` | Luba 2 left (uid 1, `session: left`) then right (uid 2, `right`) on the same edge: the gateway quits `left` with 2003 340 ms after `right`'s `join_v3`. |
| `sessions/luba2_racing_pair.json` | Luba 2 left and right joined 31 ms apart on two different edges; both survive and subscribe; closed by the host. |
| `gateway/real/join_v3.json` | The library's `join_v3` (luba2_single), with the browser's client ORTC. |
| `gateway/real/join_ok_luba2.json`, `join_ok_luba3_vision.json` | Join results: `attributes`, `ortc` (one `sendrecv` bucket, rtx for every video codec, role `client`, one fingerprint), `rejoin_token`, `return_vosip`, `uid`, `vid`. No `cid`, `cname` or stream list. |
| `gateway/real/on_rtp_capability_change.json` | Sent right after every join result. |
| `gateway/real/on_user_online.json` | `{uid}` only. |
| `gateway/real/on_add_video_stream.json`, `on_add_video_stream_second_publisher.json` | `{cname, pt, rtxSsrcId, ssrcId, uid, video}`: no `codec`, no `uint_id`; ssrcs are per viewer, allocated in announcement order. |
| `gateway/real/subscribe.json`, `subscribe_ack.json` | The library's subscribe and its ack `{p2pid, uid}`. |
| `gateway/real/on_p2p_ok.json` | `{proxy: true, uid}`. |
| `gateway/real/ping.json`, `ping_reply.json`, `leave.json` | The reply is `{_id, _result}` with no `_message`. |
| `gateway/real/on_notification_quit_repeat_join.json` | The 2003 quit, with `option: ""`. |
| `ap/real/choose_server_request.json` | The library's URI 22 request. |
| `ap/real/choose_server_response.json`, `choose_server_response_turn_first.json` | Both block orders occur; top-level `detail {502}`, `wan_ip`, `leave_ts`. |

### Q20 experiments

CAPTURED 2026-10-01 21:20–21:23, Luba 2, same account and channel, for Q20
(`docs/open_questions.md`, D33). Normalised as above with the same address
map, continued: new gateway edges `203.0.113.22`–`.24`, new TURN edge
`198.51.100.28`, the gateway's IPv6 host candidate `2001:db8::9`. The token's
uid + 1, which run 1 sent on purpose, is `123457`.

| File | Content |
|---|---|
| `sessions/luba2_made_up_uid.json` | Run 1. `left` (target uid 1) joins and pings. `right` asks the AP with the real uid (answered), then with uid + 1 and the same token; both blocks come back `2010009` and no join follows. The host log then shows `left` ending `socket_closed` after an unanswered ping (21:20:47.99). |
| `ap/real/choose_server_rejected_no_authorized.json` | Run 1's uid + 1 answer: `code` 2010009 per block, `cert` `""`, `detail` `{10}` only, no `edges_services`. |
| `sessions/luba2_two_edges.json` | Run 2. `left` (uid 1) on the AP's first edge; 17 s later `right` (uid 2, `gateway_edge_offset=1`) on its AP answer's second edge, same viewer uid. Both subscribe and ping with no quit until the last frame, 43.9 s after `right`'s join. The host log then shows both sockets closing 2 ms apart, with no frame first. |

`edge` (`ip:port`, gateway entries of `luba2_two_edges.json` only) is the edge
the session dialled. The capture logs no URL, so it is read from the AP
answer at the session's offset and confirmed twice by its join result: the
host candidate equals it, and the DTLS fingerprint equals the AP's detail-19
entry at that index, for both viewers. Session attribution follows
the rules above: `opid` for AP exchanges, `_id`, the ping phase (`left`
pings at `.04x`–`.08x` s, `right` at `.10x`–`.13x` s), and the session
logger's `Ignoring a stream from uid 1 (… target 2)` for `right`'s uid 1
announcement.

## gateway/

The inbound files below were RECONSTRUCTED before the capture. Those the
capture contradicted now carry the captured key set with their illustrative
values (RECONSTRUCTED, captured shape); the rest stay reconstructed for the
cases no capture shows (failures, tolerance, PetKit). The outbound
`*_expected.json` files are CODE: the exact frames the shipped HA-Luba
builders sent, with the D7/D20 changes applied; `gateway/real/` confirms the
subscribe, ping, leave and join shapes. Ids, timestamps and credentials are the
fixed test values in `join_v3_inputs.json` and `tests/_helpers.py`.

| File | Label | Source and edits |
|---|---|---|
| `join_ok.json` | RECONSTRUCTED, captured shape | `join_ok_luba2.json`'s key set (`attributes`, `ortc`, `rejoin_token`, `return_vosip`, `uid`, `vid`) around a trimmed ORTC without rtx (candidate `192.0.2.13`, stub fingerprint). Superseded as a reference by `real/join_ok_*.json`. |
| `join_ok_rtx.json` | RECONSTRUCTED, captured shape | As `join_ok.json` with an `rtx` video codec (the captured gateway lists rtx for every video codec) and no `rejoin_token`. |
| `join_ok_no_ortc.json` | RECONSTRUCTED, captured shape | A success without `ortc`. |
| `join_ok_with_streams.json` | RECONSTRUCTED | The pre-capture `join_ok.json`: `cid`, `cname` and a `streams` list with one video and one audio-only entry, for PetKit's walk. No captured join carries any of them. |
| `on_add_video_stream.json` | RECONSTRUCTED, captured shape | `real/on_add_video_stream.json`'s keys with the old values (ssrc 44444444, pt 102). Superseded by it as a reference. |
| `on_user_online.json` | CAPTURED shape | Identical in shape to `real/on_user_online.json`. |
| `on_user_offline.json` | RECONSTRUCTED | §3.2; no `on_user_offline` was captured, so `reason` is unverified. |
| `on_notification_quit.json` | CAPTURED shape | `real/on_notification_quit_repeat_join.json`, which it now equals. |
| `on_notification_warn.json` | CAPTURED shape | §5, from `tests_ha/test_agora_session_quit.py`. |
| `on_p2p_ok.json`, `on_rtp_capability_change.json` | CAPTURED shape | Equal to their `real/` files (`on_p2p_ok` with the fixture uid). |
| `ping_back.json` | CAPTURED shape | The reply to `ping` (`real/ping_reply.json`), test ids; the name predates the capture. |
| `subscribe_ack.json` | CAPTURED shape | `real/subscribe_ack.json` with the test ids. |
| `join_failed.json` | SDK | §5 failed-request shape (`error_code`, `error_str`). |
| `join_failed_code_only.json` | SDK | The SDK's `error_code \|\| code` fallback, with the code as a string (`Number()` coerces it). |
| `on_add_video_stream_h265_pt0.json` | RECONSTRUCTED | §3.2 verbatim: H265 publisher, `pt: 0`. |
| `on_add_video_stream_minimal.json` | SDK | The rejoin replay variant `{uid, uint_id, video, ssrcId}` (§3.2). |
| `on_add_video_stream_no_ssrc.json` | RECONSTRUCTED | Missing `ssrcId`: the tolerance case. |
| `on_user_offline_no_uid.json` | RECONSTRUCTED | Missing `uid`: the tolerance case. |
| `on_p2p_lost.json` | SDK | §5, fields in `_message` as the §2.1 envelope implies. |
| `on_p2p_lost_top_level_only.json` | RECONSTRUCTED | Fields only at the top level, HA-Luba's reading; pins the fallback. |
| `on_p2p_lost_top_level.json` | RECONSTRUCTED | Fields in both places with different values, the placement HA-Luba's handler read (§5); pins that `_message` wins. |
| `on_rtp_capability_change_irregular.json` | RECONSTRUCTED | Non-string codec entries and non-boolean flags: the tolerance case. |
| `on_token_privilege_will_expire.json`, `on_token_privilege_did_expire.json` | RECONSTRUCTED | §4; payloads unverified and unread. |
| `error.json` | CODE | §5 `error` event. |
| `unknown_event.json` | RECONSTRUCTED | An SDK event the library does not handle (`on_published_user_list`) with an extra top-level key. |
| `non_object_message.json` | RECONSTRUCTED | An event whose `_message` is not an object. |
| `join_v3_inputs.json` | CODE | The inputs `join_v3_expected.json` is built from: fixed `_id`, session id, process id, `join_ts`, a small client ORTC and the flag-4096 AP block. |
| `join_v3_expected.json` | CODE | §2.3 with D7 applied: flags nested under `attributes.userAttributes`. The `browser` value writes the last dot of the four-part Chrome version as the JSON escape `\u002e`, so the fixture address check does not read the version as an IPv4 address; it decodes to the shipped string. |
| `subscribe_expected.json`, `unsubscribe_expected.json`, `set_client_role_expected.json` | CODE | §3.3. |
| `ping_expected.json`, `leave_expected.json`, `renew_token_expected.json` | CODE | §4. |

## ap/

| File | Label | Source and edits |
|---|---|---|
| `choose_server_request_expected.json` | CODE | The library's URI 22 request with the test credentials; `ap/real/choose_server_request.json` has the same shape. |
| `choose_server_response.json` | RECONSTRUCTED, captured shape | Detail `502` moved to the top level (where `ap/real/` has it) and `candidate` dropped (never captured). The TURN block's detail `8` is `VID`: both captured blocks carry the vid there, not a TURN username. |
| `choose_server_one_failed.json`, `choose_server_all_failed.json`, `choose_server_gateway_failed.json`, `choose_server_irregular.json` | RECONSTRUCTED | Failure and tolerance cases no capture shows; pre-capture detail keys. |
| `update_ticket_response.json` | RECONSTRUCTED | No ticket refresh was captured. |
