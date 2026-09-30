# Open questions

Things no fixture, SDK reading or regression test settles. Each has the
reading the code takes today and what would close it.

## Q1. Closed: `sdp_transform` parses the extmap direction suffix

`a=extmap:12/recvonly …` decodes to `{"value": 12, "direction": "recvonly"}`
natively (sdp-transform 1.1.0); pinned by `tests/unit/sdp/test_offer.py`.

## Q2. Should the ORTC carry a DTLS role at all?

The JS SDK sends none; HA-Luba's `server` fixed a deadlock; PetKit's
`client` works. Today: `server` (D4), option to send none. Closes with a
capture of a join where no role is sent and DTLS still completes, on both
vendors.

## Q3. Do PetKit devices need `set_client_role` after join?

PetKit's copy sends it; Mammotion mowers leave when it is sent. Today: off
by default (D6). Closes with a PetKit session run with the option off.

## Q4. Is there a gateway message for post-join trickle candidates?

The WHEP PATCH path accepts them and drops them. Today: candidates are only
sent in the join (D11). Closes by reading the JS SDK's ICE restart / trickle
path or a capture.

## Q5. Do the AP detail `8`/`4` TURN credentials ever work, and which port?

HA-Luba saw 401s and switched to uid-derived credentials; PetKit tries the
detail ones first. Today: uid by default (D12). `get_ice_servers` also
builds `turn:` URLs on the fixed ports 3478/443 and ignores the port the AP
returns for the TURN edge, as both copies shipped. Closes with a TURN
allocation trace on each vendor.

## Q6. Does any current device set `openEncrypt`?

If a Mammotion token arrives with `openEncrypt != 0` the stream cannot
decode in a plain WebRTC consumer. Today: fields modelled, never sent, not
decrypted (D20, Q17). Closes with a look at real token responses.

## Q7. Should the MID extension also be stripped from the ORTC?

The memory note said both; only the answer was ever stripped. Today: answer
only (D16). Closes with an A/B capture of RTP demux behaviour.

## Q8. Does a plain `a=msid` in the answer help browsers?

The "stream id 1" fix never landed and video works without it. Today: no
`msid` unless a remote stream is declared (D17). Closes with a browser
transceiver dump comparison.

## Q9. Which AP request details matter?

`11`, `17`, `22` are sent; `6` (string uid) is sent when a string uid is
known; `area_code` is now taken from the credentials. Today: that set.
Closes with the JS SDK's `chooseServer` argument list cross-checked against
a capture.

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
advises hosts to leave the default. Closes with an AP request using the
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

`tests/fakegateway` had to choose, without a capture: the join payload's
existing-stream key (`streams`), the join failure codes (2013 app id, 2014
channel, 110 token, 2022 missing key), the ping reply (a success response
with an empty `_message`, per protocol §4), subscribe error codes (2011
before join, 2021 unknown stream), the `on_user_offline` reason (`quit`),
the AP response `uri` (request uri + 1) and its detail keys (detail 19 as
`sha-256 <fingerprint>` per edge), and the RTM failure `code` strings. Each closes with one real capture of that exchange;
when the real shape differs, the fake changes first (testing.md §6).

## Q18. Does `on_user_online` always accompany `on_add_video_stream`?

The session subscribes once both have been seen (the Mammotion rule;
protocol §3.2). PetKit's copy subscribed on the stream announcement alone.
If a PetKit gateway announces the stream without a preceding presence event,
no subscribe is sent. Closes with a PetKit gateway capture; if needed an
option relaxes the gate.

## Q19. Does the gateway ever omit `dtlsParameters.fingerprints`?

Every reconstructed join response carries one, yet both hosts merged in the
AP's detail-19 fingerprints (HA-Luba's comment credits it with fixing DTLS
that sent but never received). D26 fills them in only when the gateway sends
none, which is what the merge changed in the answer. Closes with a capture of
a join response without them, or of detail 19 whose value differs from the
gateway's; the fake cannot yet omit them (backlog).

