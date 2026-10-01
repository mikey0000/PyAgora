# Open questions

Things no fixture, SDK reading or regression test settles. Each has the
reading the code takes today and what would close it.

## Q1. Closed: `sdp_transform` parses the extmap direction suffix

`a=extmap:12/recvonly …` decodes to `{"value": 12, "direction": "recvonly"}`
natively (sdp-transform 1.1.0); pinned by `tests/unit/sdp/test_offer.py`.

## Q2. Should the ORTC carry a DTLS role at all?

The JS SDK sends none; HA-Luba's `server` fixed a deadlock; PetKit's
`client` works. Today: `server` (D4), option to send none. Narrowed by the
2026-10-01 capture: with `server` the Mammotion gateway answered role
`client` and DTLS completed (`on_p2p_ok`) in all six sessions
(`fixtures/gateway/real/join_ok_luba2.json`). Still open: a join with no
role, and PetKit.

## Q3. Do PetKit devices need `set_client_role` after join?

PetKit's copy sends it; Mammotion mowers leave when it is sent. Today: off
by default (D6). Closes with a PetKit session run with the option off.

## Q4. Is there a gateway message for post-join trickle candidates?

The WHEP PATCH path accepts them and drops them. Today: candidates are only
sent in the join (D11). Closes by reading the JS SDK's ICE restart / trickle
path or a capture.

## Q5. Closed: the AP detail `8`/`4` values are not TURN credentials

The TURN block's detail `8` is the `vid`, identical to the gateway block's, in all eight captured responses
(`fixtures/ap/real/choose_server_response.json`), and the SDK reads it only as `vid` and derives TURN
credentials from the uid alone (D32). `DETAIL_FIRST` was sending the vid as the username, which explains
HA-Luba's 401s; it now gives the uid pair and is deprecated. With uid-derived credentials the browser's join
ORTC listed relay candidates on the TURN edges. Detail `4` is a token-shaped value the SDK does not read.
The fixed TURN ports are a backlog item.

## Q6. Does any current device set `openEncrypt`?

If a Mammotion token arrives with `openEncrypt != 0` the stream cannot
decode in a plain WebRTC consumer. Today: fields modelled, never sent, not
decrypted (D20, Q17). Narrowed: all eight `stream/token` responses in the
2026-10-01 HA log (Luba 2, Luba 3) carried `openEncrypt: 0`. Still open for
Yuka, 4G models and PetKit.

## Q7. Should the MID extension also be stripped from the ORTC?

The memory note said both; only the answer was ever stripped. Today: answer
only (D16). Closes with an A/B capture of RTP demux behaviour.

## Q8. Does a plain `a=msid` in the answer help browsers?

The "stream id 1" fix never landed and video works without it. Today: no
`msid` unless a remote stream is declared (D17). Closes with a browser
transceiver dump comparison.

## Q9. Which AP request details matter?

`11`, `17`, `22` are sent; `6` (string uid) is sent when a string uid is
known; `area_code` is now taken from the credentials. Narrowed: for
Mammotion, `{"11": "CN,GLOBAL", "17": "1", "22": "CN,GLOBAL"}` alone got
code 0 for both blocks in all eight requests
(`fixtures/ap/real/choose_server_request.json`). Still open: whether `6`
matters for PetKit (backlog), and the SDK's `chooseServer` argument list.

## Q10. `enableInstantVideo` and `enablePreallocPC`

The SDK forces `enablePreallocPC = true`; PetKit sent `false` and works.
`enableInstantVideo` is PetKit's option. Today: `true` and an option. Closes
with captures showing any behavioural difference.

## Q11. Renew debounce: 30 s or none?

HA-Luba debounces the ~1 s `will_expire` notices; PetKit renews each time
with a fresh token. Today: 30 s (D8). Closes with a gateway log showing
whether repeated renews are penalised.

## Q12. What form does the AP accept for `area_code`?

Mammotion's token response carries `areaCode: "AREA_CODE_EU"`, an Android
SDK enum name; the Web SDK sends `"CN,GLOBAL"`-style lists. Today: the
library sends whatever the credentials carry, and the migration guide
advises hosts to leave the default. The 2026-10-01 capture sent the default
`CN,GLOBAL` for tokens whose `areaCode` was `AREA_CODE_EU` and
`AREA_CODE_GLOB`; every request succeeded. Closes with an AP request using the
enum form and its response.

## Q13. What does `on_p2p_lost` mean for a subscribe-only client?

Mammotion ignored it; PetKit treats it as fatal. Today: an option (D22).
Closes with a capture of the frame's `_message` during a healthy and a
broken stream.

## Q14. RTM ack shapes are reconstructed

No RTM response was ever captured; the `result`/`code` values and the
failure code string come from PetKit's parser. Today: `success` +
`message_sent`/`message_delivered` accepted, compared case-insensitively.
Closes with one captured ack of each kind.

## Q15. Should the client ORTC carry a `cname`?

`offer_to_ortc` includes `cname` when the offer carries an `a=ssrc` cname
line. No shipped copy sent it (their offers were recvonly and had none) and
the SDK's offer-to-ORTC sends none. Today: sent when present. Closes with a
capture of a join carrying it.

## Q17. How exactly does the SDK send channel encryption in `join_v3`?

`agoraRTC_N-4.24.3.js:63522-63558` (in `HA-Luba/`) replaces the password
with `base64(RSA-OAEP-SHA256(spki_key, secret))`, the key being a base64
SPKI blob embedded in the SDK; `:46356-46362` then writes `aes_mode`,
`aes_secret` (the wrapped value) and, behind the `ENCRYPT_AES` feature flag,
`aes_encrypt: true`, plus `aes_salt` when given. Today: no `aes_*` field is
sent and a WARNING is logged (D20). Closes with a capture of an encrypted
join, and only matters once a consumer that can decrypt exists.

## Q16. Shapes the fake gateway reconstructs

Closed by the 2026-10-01 capture, and the fake changed to match
(testing.md §6): the join result lists **no** existing streams under any key
(publishers already in the channel are announced by events right after it),
and carries `attributes`, `ortc`, `rejoin_token`, `return_vosip`, `uid`,
`vid` but no `cid`/`cname`; the ping reply is `{_id, _result}` with no
`_message`; the subscribe ack is `{p2pid, uid}`; `on_add_video_stream` has no
`codec` or `uint_id`; the quit carries `option: ""`; the AP response `uri` is
the request's + 1, with top-level `detail {502}` and `wan_ip`, and detail 19
holds bare fingerprints each followed by `;`; the gateway takes DTLS role
`client` and its fingerprint is the AP's for that edge
(`fixtures/gateway/real/`, `fixtures/ap/real/`). Still reconstructed: the
join failure codes (2013, 2014, 110, 2022), subscribe error codes (2011,
2021), the `on_user_offline` reason (`quit`), and the RTM failure `code`
strings; each closes with one capture of that exchange.

## Q18. Does `on_user_online` always accompany `on_add_video_stream`?

The session subscribes once both have been seen (the Mammotion rule;
protocol §3.2); publishers found in the join payload count as online.
Closed for Mammotion: in all eleven captured announcements
`on_user_online` came first, 0 ms to 2.0 s before the stream, and a
publisher can be online without ever publishing
(`fixtures/sessions/luba3_vision.json`). Still open for PetKit: its copy
subscribed on `on_add_video_stream` alone and on the join-payload walk
(`agora_websocket.py:433-470`, `:555-566`), and RTM `start_live` goes out
just before the join, so the camera likely starts publishing after we
joined. Today: gated by default; `SessionOptions.subscribe_requires_online
=False` drops the gate and PetKit passes it (D28). Closes with a
default-mode PetKit session at DEBUG (migration §4): a `Holding stream …
from uid … until on_user_online` line with no subscribe after it is the
gate holding the stream.

## Q19. Does the gateway ever omit `dtlsParameters.fingerprints`?

Both hosts merged in the AP's detail-19 fingerprints (HA-Luba's comment
credits it with fixing DTLS that sent but never received); D26 fills them in
only when the gateway sends none. Narrowed: the Mammotion gateway sent one
`sha-256` fingerprint in all six captured joins, equal to the AP's detail-19
entry for the connected edge (`fixtures/sessions/*.json`), so for Mammotion
the merge never changed the answer. Still open for PetKit; the fake now
derives both from one value but cannot omit them (backlog).

## Q20. Closed: the 2003 repeat-join eviction is per gateway edge

Same uid, same edge: the gateway quit the older viewer with 2003 340 ms after the newer `join_v3`
(`fixtures/sessions/luba2_left_then_right.json`). Same uid, different edges, joined 17 s apart with the first
long settled: no quit, no `on_user_offline`, both answering pings 43.9 s after the second `join_v3`
(`fixtures/sessions/luba2_two_edges.json`, protocol §5.3; the second viewer used `gateway_edge_offset=1`).
So it was the edge, not the racing, that let `luba2_racing_pair.json` survive. A made-up viewer uid is not an
alternative: the AP answers the token with uid + 1 with `2010009` (`NO_AUTHORIZED`) for both services, empty
`cert`, no edges (`fixtures/ap/real/choose_server_rejected_no_authorized.json`,
`fixtures/sessions/luba2_made_up_uid.json`). Two racing joins on one edge were not tried. Decision: D33.
