# Protocol

> Provenance: §1–§5 are CAPTURED from six Mammotion viewer sessions recorded on 2026-10-01 (a Luba 2 and a
> Luba 3, through Home Assistant, with the `pyagorartc.capture` logger at DEBUG, D30). Every sample is a real
> frame normalised as `tests/fixtures/README.md` describes, and names the fixture it comes from. What the
> capture did not show is still marked: RTM (§8, Mammotion does not use it), anything PetKit-specific,
> channel encryption, and frames that never occurred (failures, `on_user_offline`, token expiry, `p2p_lost`).

Collected read-only from HA-Luba, Luba-API, the PetKit integration, the Mammotion 2.3.8.201 APK tree and the
Agora Web SDK bundle that HA-Luba keeps (`/home/michael/git/HA-Luba/agoraRTC_N-4.24.3.js`, 84 200 lines,
untracked), then checked against the capture.

## Provenance labels

| Label | Meaning |
|---|---|
| **CAPTURED** | Recorded from a real session, redacted, normalised; the fixture is named. |
| **CODE** | The exact shape our Python sends or parses, taken from source. |
| **SDK** | Read directly from `agoraRTC_N-4.24.3.js` at the line cited. |
| **SDK knowledge, unverified** | From general knowledge of the Agora Web SDK. No local source confirms it. |
| **GENERATED** | Output of HA-Luba's own code on real inputs, produced for this document (see §6.3). |

The capture: six `join_v3` sessions in four scenarios, all on WiFi (no Mammotion keep-alive), no RTM.

| Scenario | Fixture | Viewers (target uid) | Ended |
|---|---|---|---|
| Luba 2, one camera | `sessions/luba2_single.json` | one (uid 2) | host close after 46 s |
| Luba 3 "Vision camera" | `sessions/luba3_vision.json` | one (uid 1) | host close after 26 s |
| Luba 2 left, then right | `sessions/luba2_left_then_right.json` | left (1), right (2), same edge | left: gateway quit 2003; right: host close |
| Luba 2 left and right together | `sessions/luba2_racing_pair.json` | left (1), right (2), joins 31 ms apart, two edges | both host close after ~31 s |

## Redaction rules applied

Fixtures and the samples below replace secrets with obviously fake values and addresses with documentation
ranges; the full mapping is in `tests/fixtures/README.md`. In short: channel names → `channel-test`, the viewer
uid → `123456`, channel ids → `123456789` / `123456788`, `vid` → `987654`, gateway edges → `203.0.113.10`+, TURN
edges → `198.51.100.20`+, the caller's public address → `192.0.2.1`, AP detail `502` → `192.0.2.2`, tokens →
`rtc-token-not-real`, tickets → `TICKET_REDACTED`, ICE passwords → `ice-pwd-gateway-test-001`. Timestamps,
`_id`s, ports, ssrcs and DTLS fingerprints are as recorded.

---

## 1. AP discovery (`choose_server`, URI 22) and `update_ticket` (URI 28)

**Endpoint** (CODE, `HA-Luba/custom_components/mammotion/agora_api.py:503-513, 834-848`):
`POST https://webrtc2-ap-web-{1..4}.agora.io/api/v2/transpond/webrtc?v=2`. The backups are `-5` and `-6`.
The body is `multipart/form-data` with one field, `request`, holding the JSON below. With a cloud proxy the URL
becomes `https://{proxy}/ap/?url={domain}/api/v2/transpond/webrtc?v=2`.

### 1.1 Request (CAPTURED, `ap/real/choose_server_request.json`)

```json
{"appid": "app-id-test", "client_ts": 1790812070833, "opid": 43238050022, "sid": "47759297",
 "request_bodies": [{"uri": 22, "buffer": {
   "cname": "channel-test", "detail": {"11": "CN,GLOBAL", "17": "1", "22": "CN,GLOBAL"},
   "key": "rtc-token-not-real", "service_ids": [11, 26], "uid": 123456}}]}
```

- `opid` is a random int below 10¹², `sid` a random int below 2³¹ sent as a string (CODE, `agora_api.py:731-772`).
- `detail` keys `11` and `22` carry the area list, `17` the role (`"1"` host). This set, and nothing else, was
  accepted for every Mammotion request (Q9). The SDK also sends `"6": stringUid` and `"12": "1"`; PetKit sends
  `"6"` (`petkit/agora_api.py:370-376`), which the capture does not exercise.
- `update_ticket` (URI 28) has the same envelope plus `buffer.edges_services: [{ip, port}, ...]`
  (`agora_api.py:606-673`). No ticket refresh was captured.

### 1.2 Service id to response `flag` mapping (SDK `agoraRTC_N-4.24.3.js:34376-34379`, `43683-43700`)

| Request `service_ids` entry | Name | Response `buffer.flag` | Hex | Carries |
|---|---|---|---|---|
| 11 | CHOOSE_SERVER | 4096 | `0x001000` | WebSocket gateway edges |
| 18 | CLOUD_PROXY | 1048576 | `0x100000` | proxy3 edges |
| 20 | CLOUD_PROXY_5 | 4194304 | `0x400000` | proxy5 edges |
| 26 | CLOUD_PROXY_FALLBACK | 4194310 | `0x400006` | TURN edges |

The flags behave like tags rather than a bitmask: the SDK matches `buffer.flag === value` exactly. The captured
responses answer `[11, 26]` with one 4096 and one 4194310 block each.

### 1.3 Response (CAPTURED, `ap/real/choose_server_response.json`)

```json
{"detail": {"502": "192.0.2.2"}, "enter_ts": 1790812070854, "leave_ts": 1790812071206,
 "opid": 43238050022, "wan_ip": "192.0.2.1",
 "response_body": [
  {"uri": 23, "buffer": {"cert": "TICKET_REDACTED", "cid": 123456789, "cname": "channel-test", "code": 0,
    "flag": 4096, "uid": 123456,
    "detail": {"1": "192.0.2.1", "10": "DETAIL_10_REDACTED",
               "19": "C1:F3:47:…:8A:9E;FA:8B:FF:…:55:A3;6E:C4:67:…:0A:DA;",
               "2": "NA", "23": "", "3": "XX", "4": "TURN_CRED_REDACTED", "8": "987654", "9": ""},
    "edges_services": [{"ip": "203.0.113.10", "port": 4705}, {"ip": "203.0.113.11", "port": 4711},
                       {"ip": "203.0.113.12", "port": 4701}]}},
  {"uri": 23, "buffer": {"cert": "TURN_TICKET_REDACTED", "cid": 123456789, "cname": "channel-test", "code": 0,
    "flag": 4194310, "uid": 123456,
    "detail": {"1": "192.0.2.1", "10": "DETAIL_10_REDACTED", "2": "NA", "23": "", "3": "XX",
               "4": "TURN_CRED_REDACTED", "8": "987654", "9": ""},
    "edges_services": [{"ip": "198.51.100.20", "port": 443}, {"ip": "198.51.100.21", "port": 443},
                       {"ip": "198.51.100.22", "port": 443}]}}]}
```

What the eight captured responses show:

- **Block order varies.** Three put the gateway block first, five the TURN block
  (`ap/real/choose_server_response_turn_first.json`); the response `uri` is the request's plus one.
- **Top level.** `detail` holds only `502`; `wan_ip` is the caller's public address and equals block detail `1`.
  `enter_ts`/`leave_ts` bracket the AP's processing (100–350 ms).
- **Detail `19`** is in the gateway block only: one bare fingerprint per edge (no `sha-256 ` prefix), each
  followed by `;`, so the value ends in `;`. In all six joins the gateway's join-result fingerprint equals the
  detail-19 entry at the index of the edge connected to (Q19).
- **Detail `8` is the `vid` in both blocks.** The TURN block carries no TURN username; the TURN credentials are
  uid-derived, as the SDK derives them (§1.4, D32).
- **Detail `10`** is an AccessToken2-shaped `007e…` value in both blocks; nothing reads it. The capture logger
  did not redact it; it now does (D30).
- **Detail `4`** is non-empty in both blocks, a token-shaped value (redacted, D30). The SDK does not read it.
- Gateway edge ports vary per edge (3478, 4700–4733); TURN edges are on 443. The edge list changed from one
  request to the next, though two answers 27 s apart kept the same first edge.

What each `detail` key means, as the SDK reads it:

| Key | Meaning | Source |
|---|---|---|
| `1` | `uni_lbs_ip`; captured: the caller's public address | SDK:43632; capture |
| `4` | not read by the SDK; captured: a token-shaped value in both blocks | capture |
| `8` | `vid`; captured: identical in both blocks; not a TURN username (D32) | SDK:43631, 43455; capture |
| `18` | per-address IPv6, `;`-separated (not captured) | SDK:43604 |
| `19` | per-address DTLS fingerprint, `;`-separated, matched to addresses by index; captured bare | SDK:48698-48704; capture |
| `23` | area; captured empty | SDK:48689 |
| `38` | cross-region tag (not captured) | SDK:48512 |
| `502` | `csIp`, used in error reports; captured at the top level only | SDK:43634 |
| `candidate` | `"ip:port"`, the AP-suggested gateway; not in any captured response | SDK:43611-43620 |

- The top-level `detail` is merged under each buffer's `detail` (SDK:43726); the captured join's `ap_response`
  shows the result, with `502` first.
- A buffer with `code == 0` and no `edges_services` was not captured (SDK:48589-48610).

#### Rejected response (CAPTURED, `ap/real/choose_server_rejected_no_authorized.json`)

Q20 run 1 asked with the real token and the token's uid + 1. The AP refused both services:

```json
{"detail": {"502": "192.0.2.2"}, "enter_ts": 1790842823214, "leave_ts": 1790842823502,
 "opid": 378880333257, "wan_ip": "192.0.2.1",
 "response_body": [
  {"uri": 23, "buffer": {"cert": "", "cid": 123456789, "cname": "channel-test", "code": 2010009,
    "detail": {"10": "DETAIL_10_REDACTED"}, "flag": 4096, "uid": 123457}},
  {"uri": 23, "buffer": {"cert": "", "cid": 123456789, "cname": "channel-test", "code": 2010009,
    "detail": {"10": "DETAIL_10_REDACTED"}, "flag": 4194310, "uid": 123457}}]}
```

- One `code` per service, the same in both blocks. `cert` is present and empty; there is no `edges_services`;
  `detail` holds only `10`. The top level (`502`, `wan_ip`, timestamps) is as in a success.
- The SDK splits a code into a service, `code // 10000`, and a reason, `code % 10000`, and looks both up in one
  table (`Ux`, SDK:29449-29477; table `Mx`, SDK:29305-29448). Services are `NV` (SDK:28191-28197): 101
  `ACCESS_POINT`, 201 `UNILBS`, 901 `STRING_UID_ALLOCATOR`. So 2010009 is UNILBS reason 9, `PV.NO_AUTHORIZED`,
  "invalid token, authorized failed", `retry: false`. The token is bound to its uid.
- The choose_server parser calls `Ux` on every non-zero block code (SDK:48612-48627). A failed TURN block is only
  logged; a failed gateway block fails the request. The update-ticket parser does the same (SDK:48739-48749).
- An unknown 101 reason gets its code as the description; it is retried when the reason starts with `2`.

UNILBS reasons (`PV`, SDK:28207-28225; descriptions SDK:29380-29434), all `retry: false`. The library names
them in `APRejectedError`'s message (`ap.describe_ap_code`, table `AP_RESPONSE_CODE_NAMES`):

| Code | `PV` name | SDK description |
|---|---|---|
| 2010005 | `INVALID_VENDOR_KEY` | invalid vendor key, can not find appid |
| 2010007 | `INVALID_CHANNEL_NAME` | invalid channel name |
| 2010008 | `INTERNAL_ERROR` | unilbs internal error |
| 2010009 | `NO_AUTHORIZED` | invalid token, authorized failed (captured) |
| 2010010 | `DYNAMIC_KEY_TIMEOUT` | dynamic key or token timeout |
| 2010011 | `NO_ACTIVE_STATUS` | no active status |
| 2010013 | `DYNAMIC_KEY_EXPIRED` | dynamic key expired |
| 2010014 | `STATIC_USE_DYNAMIC_KEY` | static use dynamic key |
| 2010015 | `DYNAMIC_USE_STATIC_KEY` | dynamic use static key |
| 2010016 | `USER_OVERLOAD` | amount of users over load |
| 2010018 | `FORBIDDEN_REGION` | the request is forbidden in this area |
| 2010019 | `CANNOT_MEET_AREA_DEMAND` | unable to allocate services in this area |
| 2010027 | `REQ_DOWNGRADE_FALLBACK` | request downgrade fallback |

### 1.4 What is derived from the AP response

**WebSocket URL** (CODE, `agora_websocket.py:268`): `wss://{ip with '.'→'-'}.edge.agora.io:{port}`, taken from
the flag-4096 edges in order. Every captured session connected to the first edge. The SDK can also use an
`edge.sd-rtn.com` dual domain and a port-443-only mode (`JOIN_GATEWAY_USE_DUAL_DOMAIN` / `USE_443PORT_ONLY`,
SDK:30784-30785); those host patterns are SDK knowledge, unverified.

**`ap_response` inside `join_v3`** (CAPTURED, `gateway/real/join_v3.json`): the flag-4096 buffer reshaped,
`enter_ts` as `server_ts`, `cert` doubled as `ticket`, the merged `detail`:

```json
{"code": 0, "server_ts": 1790812070854, "uid": 123456, "cid": 123456789, "cname": "channel-test",
 "detail": {"502": "192.0.2.2", "1": "192.0.2.1", "10": "DETAIL_10_REDACTED", "19": "C1:F3:…;FA:8B:…;6E:C4:…;",
            "2": "NA", "23": "", "3": "XX", "4": "TURN_CRED_REDACTED", "8": "987654", "9": ""},
 "flag": 4096, "opid": 43238050022, "cert": "TICKET_REDACTED", "ticket": "TICKET_REDACTED"}
```

**ICE servers handed to the browser** (CODE, `agora_api.py:298-324`). Each TURN edge expands into three entries:
UDP and TCP on `turn:{ip}:3478`, TLS on `turns:{a-b-c-d}.edge.agora.io:443?transport=tcp`. The credential is
`sha256(str(uid)).hexdigest()` with `str(uid)` as the username (`agora_api.py:39-55`; the SDK's
`ENCRYPT_PROXY_USERNAME_AND_PSW`, SDK:43740-43755). The browser's captured join ORTC lists relay candidates on
the TURN edges, so allocations with these credentials succeeded. Test vector:
`derive_password(12345678) == hashlib.sha256(b"12345678").hexdigest()`.

**SDK-style `turnServer` object** (CODE, `agora_api.py:350-405`):

```json
{"mode": "manual",
 "servers": [{"turnServerURL": "198.51.100.20", "tcpport": 443, "udpport": 443, "username": "123456",
              "password": "SHA256_OF_UID", "forceturn": false, "security": true}],
 "serversFromGateway": [{"username": "123456", "password": "rtc-token-not-real", "turnServerURL": "203.0.113.10",
                         "tcpport": 4735, "udpport": 4735, "forceturn": false}]}
```

For `serversFromGateway`, the port is the gateway port + 30 and the password is the channel token itself. That
entry has no `security` key. Not exercised by the capture.

---

## 2. WebSocket `join_v3`

### 2.1 Frame envelope (SDK `agoraRTC_N-4.24.3.js:30731-30745, 30800-30806, 30956-30963`; CAPTURED)

| Direction | Shape |
|---|---|
| Client request | `{"_id": "<6 hex>", "_type": "<verb>", "_message": {...}}`; `ping` and `leave` carry no `_message`. |
| Client fire-and-forget (`upload` / `send`) | `{"_type": "...", "_message": {...}}`, no `_id`. Not captured. |
| Server response | `{"_id": "<echoed>", "_message": {...}, "_result": "success"}`; the ping reply has no `_message`. |
| Server event | `{"_message": {...}, "_type": "<event>"}`, no `_id`. |
| Binary frame | Emitted as `ON_BINARY_DATA`. None captured. |

The gateway writes its keys in alphabetical order. On failure the SDK reads `Number(_message.error_code ||
_message.code)` and `_message.error_str` (SDK:30888-30897); no failure was captured.

### 2.2 Client verbs (SDK:28390-28432)

`ping`, `ping_back`, `join_v3`, `rejoin_v3`, `leave`, `set_client_role`, `publish`, `publish_datastream`,
`unpublish`, `unpublish_datastream`, `subscribe`, `pre_subscribe`, `subscribe_datastream`, `subscribe_streams`,
`unsubscribe`, `unsubscribe_datastream`, `unsubscribe_streams`, `subscribe_change`, `traffic_stats`,
`renew_token`, `set_dual_stream_mode`, `switch_video_stream`, `default_video_stream`, `set_fallback_option`,
`configure`, `gateway_info`, `control`, `send_metadata`, `data_stream`, `pick_svc_layer`, `restart_ice`,
`connect_pc`, `set_video_profile`, `set_parameter`, `set_rtm2_flag`, `downgrade_codec`.

Upload-only types (SDK:28437-28442): `wrtc_stats`, `ws_inflate_data_length`, `denoiser_stats`,
`extension_usage_stats`. The capture shows only `join_v3`, `subscribe`, `ping` and `leave`.

### 2.3 `join_v3` request (CAPTURED, `gateway/real/join_v3.json`)

```json
{"_id": "2fc3f4", "_type": "join_v3", "_message": {
  "p2p_id": 1, "session_id": "01M3TBF7J1XYAZ58J1JAYBQB2F", "app_id": "app-id-test",
  "channel_key": "rtc-token-not-real", "channel_name": "channel-test", "sdk_version": "4.24.3",
  "browser": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
  "process_id": "process-ae466eaf-e36e-32d0-c290-593d42871971", "mode": "live", "codec": "vp8", "role": "host",
  "has_changed_gateway": false, "ap_response": "<§1.4>", "extend": "", "details": {}, "features": {"rejoin": true},
  "attributes": {"userAttributes": {
    "enableAudioMetadata": false, "enableAudioPts": false, "enableNetworkQualityProbe": false,
    "enablePublishedUserList": true, "enableUserList": false, "maxSubscription": 50,
    "enableUserLicenseCheck": true, "enableRTX": true, "enableInstantVideo": false,
    "enableDataStream2": false, "enableAutFeedback": true, "enableUserAutoRebalanceCheck": true,
    "enableXR": true, "enableLossbasedBwe": true, "enableAutCC": true, "enablePreallocPC": true,
    "enablePubTWCC": false, "enableSubTWCC": true, "enablePubRTX": true, "enableSubRTX": true,
    "enableVosFallback": false, "enableQualityFallback": false, "enableDualStreamFlag": false}},
  "join_ts": 1790812074430, "ortc": "<§2.4>", "license": "license-not-real"}}
```

- This is the library's `build_join` (D7: flags nested under `attributes.userAttributes`), with `license`
  because the Mammotion token carries one. The gateway accepted it in all six sessions.
  `tests/unit/session/test_messages_captured.py` rebuilds it byte for byte from the captured inputs.
- HA-Luba's earlier Python sent the flags flat and the gateway accepted that too (not in this capture).
- `codec` is the client's codec spec, not the publisher's: `vp8` here, and the same value goes into `subscribe`
  (SDK `this.spec.codec`, HA-Luba commit `8cc8a51`). PetKit sends `h264`, `sdk_version` `4.24.0` (unverified here).
- `details` is `{}`; the SDK sends `{"6": stringUid, "cservice_map": …}` (SDK:46240-46248).
- SDK-only fields `optionalInfo` and `appScenario` are not sent.

### 2.4 Client `ortc` inside `join_v3`

The captured client ORTC (`gateway/real/join_v3.json`, from the HA frontend's Chrome offer) has
`iceParameters` (ufrag, pwd and 25 inline candidates: two mDNS host, two srflx on `192.0.2.1`, the rest
relay on the TURN edges), `dtlsParameters {fingerprints: [{hashFunction, fingerprint}], role: "server"}`,
`rtpCapabilities` with `send` empty, `recv` holding 5 video codecs (VP9 profile 1/3, AV1 profile 1, H265) and
`sendrecv` 37 video and 8 audio codecs, and `version: "2"`. It carries no `cname` (the offer had no `a=ssrc`).
The trimmed GENERATED example below is the same shape for the §6.1 offer:

```json
{
  "iceParameters": {"iceUfrag": "nNCO", "icePwd": "ICEPWD_REDACTED_24chars0"},
  "dtlsParameters": {
    "fingerprints": [{"hashFunction": "sha-256",
      "fingerprint": "69:56:A4:D2:A9:FD:9B:E5:09:63:1F:A1:96:59:04:28:E3:67:42:B2:CC:9C:26:79:15:FD:3C:0D:52:6F:7F:88"}],
    "role": "server"
  },
  "rtpCapabilities": {
    "send": {"audioCodecs": [], "audioExtensions": [], "videoCodecs": [], "videoExtensions": []},
    "recv": {"audioCodecs": [], "audioExtensions": [], "videoCodecs": [], "videoExtensions": []},
    "sendrecv": {
      "audioCodecs": [
        {"payloadType": 111, "rtpMap": {"encodingName": "opus", "clockRate": 48000, "encodingParameters": 2},
         "rtcpFeedbacks": [{"type": "transport-cc"}, {"type": "rrtr"}],
         "fmtp": {"parameters": {"minptime": "10", "useinbandfec": "1"}}}
      ],
      "audioExtensions": [{"entry": 1, "extensionName": "urn:ietf:params:rtp-hdrext:ssrc-audio-level"}],
      "videoCodecs": [
        {"payloadType": 96, "rtpMap": {"encodingName": "VP8", "clockRate": 90000},
         "rtcpFeedbacks": [{"type": "goog-remb"}, {"type": "transport-cc"}, {"type": "ccm", "parameter": "fir"},
                           {"type": "nack"}, {"type": "nack", "parameter": "pli"}, {"type": "rrtr"}],
         "fmtp": {"parameters": {}}},
        {"payloadType": 97, "rtpMap": {"encodingName": "rtx", "clockRate": 90000},
         "rtcpFeedbacks": [{"type": "rrtr"}], "fmtp": {"parameters": {"apt": "96"}}}
      ],
      "videoExtensions": [{"entry": 4, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid"}]
    }
  },
  "version": "2"
}
```

- **`fmtp` flag keys.** A key with no value becomes `null` (RED's `"111/111": null`), as in the SDK.
- **`rrtr`.** An `{"type":"rrtr"}` feedback is appended to every codec that lacks one.
- **`dtlsParameters.role`.** The library sends `"server"` (D4) and the gateway answered `"client"` in all six
  sessions. PetKit sends `"client"`; the SDK's `f2()` sends none (SDK:44146-44180). Whether "none" works is
  not captured (Q2).
- **Send/receive buckets.** H265, VP9 profile 1/3 and AV1 profile 1 go to `recv` (`agora_sdp.py:216-231`). The
  gateway's answer folds them into its single `sendrecv` bucket (§2.5).
- **MID extension.** `sdes:mid` stays in the ORTC sent to Agora; it is stripped only from the answer SDP (§6, D16).

### 2.5 `join_v3` success response (CAPTURED, `gateway/real/join_ok_luba2.json`, `join_ok_luba3_vision.json`)

```json
{"_id": "2fc3f4",
 "_message": {
  "attributes": {"userAttributes": {"subscribeAudioFilterTopN": 0}},
  "ortc": {
   "cname": "o/i14u9pJrxRKAsu",
   "dtlsParameters": {"fingerprints": [{"algorithm": "sha-256",
     "fingerprint": "C1:F3:47:CE:97:5D:F3:BD:A1:78:56:BD:E2:A9:FB:58:25:F5:59:49:F2:03:8C:19:8F:2E:52:49:52:7C:8A:9E"}],
    "role": "client"},
   "iceParameters": {"candidates": [
     {"foundation": "udpcandidate", "ip": "203.0.113.10", "port": 4705, "priority": 2103266323, "protocol": "udp", "type": "host"},
     {"foundation": "udpcandidate", "ip": "2001:db8::5", "port": 4705, "priority": 2103266323, "protocol": "udp", "type": "host"}],
    "icePwd": "ice-pwd-gateway-test-001", "iceUfrag": "123456789_ice-ufrag-gateway-test"},
   "rtpCapabilities": {"sendrecv": {
     "audioCodecs": ["opus/111", "G722/9", "PCMU/0", "PCMA/8"],
     "audioExtensions": [{"entry": 1, "extensionName": "urn:ietf:params:rtp-hdrext:ssrc-audio-level"},
                         {"entry": 2, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"},
                         {"entry": 3, "extensionName": "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"}],
     "videoCodecs": ["VP9/35", "VP9/37", "AV1/47", "H265/49", "H265/51", "VP8/96", "VP9/98", "VP9/100",
                     "H264/102", "…10 more H264/AV1…", "red/122", "ulpfec/124", "rtx/97 (apt 96)", "…20 more rtx…"],
     "videoExtensions": [{"entry": 2, "extensionName": "…abs-send-time"}, {"entry": 13, "extensionName": "urn:3gpp:video-orientation"},
                         {"entry": 3, "extensionName": "…transport-wide-cc-extensions-01"}, {"entry": 5, "extensionName": "…playout-delay"},
                         {"entry": 4, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid"},
                         {"entry": 10, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id"},
                         {"entry": 11, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id"}]}},
   "version": "2"},
  "rejoin_token": "rejoin-token-not-real",
  "return_vosip": false,
  "uid": 123456,
  "vid": 987654},
 "_result": "success"}
```

The codec lists are abbreviated `name/payloadType`; the fixture has the full objects (`fmtp`, `rtcpFeedbacks`
with `rrtr` on every codec).

- **Keys.** `attributes`, `ortc`, `rejoin_token`, `return_vosip`, `uid`, `vid`, nothing else. There is no `cid`
  (the AP block has it), no channel `cname`, and **no list of streams already published**: a publisher in the
  channel before the join is announced by `on_user_online` + `on_add_video_stream` right after the result
  (§3.2). PetKit's walk for streams in the payload finds nothing here (Q16).
- **`ortc.cname`** is `o/i14u9pJrxRKAsu` in every session and both channels; the same string is the `cname` of
  every `on_add_video_stream`.
- **ICE.** ice-lite host candidates on the connected edge, IPv4 and IPv6, same port, foundation `udpcandidate`,
  priority 2103266323. `iceUfrag` is `<cid>_` followed by base64 that embeds the channel name.
- **DTLS.** `role: "client"` in all six sessions, so the answer is `a=setup:active` (D5). One `sha-256`
  fingerprint under the key `algorithm` (the client's offer ORTC uses `hashFunction`), equal to the AP's
  detail-19 entry for the connected edge (Q19). The fill-in of D26 never triggered.

  | Server `role` | `a=setup` | Source |
  |---|---|---|
  | `server` | `passive` | SDK:44242-44251 |
  | `client` | `active` | SDK; CAPTURED (6/6) |
  | `auto` | `actpass` (SDK); `pyagorartc` answers `active` (RFC 5763 §5, D5) | SDK |

- **Codecs.** One `sendrecv` bucket only (no `send`/`recv`). It mirrors the offer's codecs, including the
  ones the client put in `recv`, and **lists an `rtx` codec for every video codec**: the earlier note that the
  live gateway offers no RTX (commit `8cc8a51`) does not hold for this offer. `offers_rtx` is true and every
  captured `subscribe` sent `rtx: true`.
- **Filling in candidates.** If `iceParameters.candidates` is empty the SDK makes one from the gateway address
  (SDK:44219-44330). Never needed in the capture.
- **Pre-subscribe SSRCs.** The SDK reads `attributes.userAttributes.preSubSsrcs` when pre-subscribe is on
  (SDK:44258-44289); the captured `userAttributes` holds only `subscribeAudioFilterTopN: 0`.

### 2.6 `rejoin_v3` (SDK:31061-31128; not captured)

The request is the join info with `token` set to the stored `rejoin_token`. The response carries a `peers`
array, and the SDK replays it as synthetic events:

```json
{"_id": "d4e5f6", "_result": "success", "_message": {
  "peers": [
    {"uid": 1, "uint_id": 1, "audio": false, "video": true, "audio_ssrc": 0, "video_ssrc": 44444444,
     "audio_mute": true, "video_mute": false, "audio_enable_local": false, "video_enable_local": true}
  ]
}}
```

The field names are SDK. The values are illustrative.

---

## 3. Subscribe and stream events

### 3.1 Server events (SDK:28446-28474)

`on_user_online`, `on_user_offline`, `on_stream_fallback_update`, `on_publish_stream`, `on_uplink_stats`,
`on_p2p_lost`, `on_remove_stream`, `on_add_audio_stream`, `on_add_video_stream`,
`on_token_privilege_will_expire`, `on_token_privilege_did_expire`, `on_user_banned`, `on_user_license_banned`,
`on_notification`, `on_crypt_error`, `mute_audio`, `mute_video`, `unmute_audio`, `unmute_video`, `on_p2p_ok`,
`receive_metadata`, `on_data_stream`, `on_rtp_capability_change`, `on_remote_datastream_update`,
`on_remote_full_datastream_info`, `enable_local_video`, `disable_local_video`, `enable_local_audio`,
`disable_local_audio`, `on_published_user_list`, `enable_multi_stream`, `on_user_list`.

The capture shows five of them: `on_rtp_capability_change`, `on_user_online`, `on_add_video_stream`,
`on_p2p_ok`, and `on_notification` (the 2003 quit). No event outside the SDK's enum appeared.

### 3.2 Event frames (CAPTURED)

`on_rtp_capability_change` (`gateway/real/on_rtp_capability_change.json`), within 7 ms of every join result
(in one racing session just after the first announcements), and in three sessions twice more ~3.0 s later:
```json
{"_message": {"extmap_allow_mixed": false, "video_codec": ["H264", "VP8"], "web_av1_svc": false},
 "_type": "on_rtp_capability_change"}
```

`on_user_online` (`gateway/real/on_user_online.json`): only the uid.
```json
{"_message": {"uid": 1}, "_type": "on_user_online"}
```

`on_add_video_stream` (`gateway/real/on_add_video_stream.json`):
```json
{"_message": {"cname": "o/i14u9pJrxRKAsu", "pt": 49, "rtxSsrcId": 40001, "ssrcId": 40000, "uid": 1, "video": true},
 "_type": "on_add_video_stream"}
```

- **Fields.** `cname` (the gateway ORTC's), `pt`, `rtxSsrcId`, `ssrcId`, `uid`, `video: true`. There is **no
  `codec`** and no `uint_id`; the parser's lower-cased `codec` is always `None` on this gateway.
- **`pt` is the payload type the gateway relays the stream on.** Every capture shows `49`, the offer's H265
  `profile-id=1`: the mowers publish H265 and the HA frontend's Chrome decodes it. `pt: 0` (no usable codec in the
  offer, commit `8cc8a51`) is not in the capture; `on_add_video_stream_h265_pt0.json` keeps that case.
- **SSRCs are per viewer, not per publisher.** Each session numbers the streams it is told about in
  announcement order: the first gets `40000`/`40001`, the second `40002`/`40003`, whichever uid it is. Uid 1 was
  `40002` in `luba2_single` and `40000` in `luba3_vision`. `subscribe.ssrcId` echoes the session's own value.
- **`on_user_online` always came first.** In all eleven announcements the publisher's `on_user_online`
  preceded its `on_add_video_stream` on the same socket, by 0 ms to 2.0 s (Q18 for Mammotion). The reverse order
  was not seen; the session's gate tolerates it.
- **A publisher can be online without a stream.** On the Luba 3 (`luba3_vision.json`), uid 2 came online
  1.6 s after the join and never published.
- **Mower uids.** Camera slot n (0-based) publishes as uid n+1 (`Luba-API/pymammotion/http/http.py:1110-1116`).
  The Luba 2 published uids 1 and 2 to every viewer; the Luba 3 published uid 1. Each viewer receives every
  publisher and filters by its target uid (`Ignoring a stream from uid …` in the session log).

`on_p2p_ok` (`gateway/real/on_p2p_ok.json`), 0.96–4.35 s after the join result, once per session:
```json
{"_message": {"proxy": true, "uid": 123456}, "_type": "on_p2p_ok"}
```
`uid` is the viewer's join uid. `proxy` was `true` in all six sessions; what it distinguishes is unverified.

Not captured: `on_user_offline` (no publisher left during a session; `reason` values unverified),
`on_add_audio_stream`, `on_remove_stream`, the mute events. Reconstructed shapes for the first two:
```json
{"_type": "on_user_offline", "_message": {"uid": 1, "reason": "quit"}}
{"_type": "on_add_audio_stream", "_message": {"uid": 1, "uint_id": 1, "audio": true, "ssrcId": 55555555}}
```

### 3.3 `subscribe` / `unsubscribe` / `set_client_role`

`subscribe` (CAPTURED, `gateway/real/subscribe.json`) and its ack (`gateway/real/subscribe_ack.json`), 295 ms to
2.0 s later:
```json
{"_id": "ffdb8b", "_type": "subscribe", "_message": {
  "stream_id": 1, "stream_type": "video", "mode": "live", "codec": "vp8",
  "p2p_id": 1, "twcc": true, "rtx": true, "extend": "", "ssrcId": 40000}}
{"_id": "ffdb8b", "_message": {"p2pid": 1, "uid": 123456}, "_result": "success"}
```

The ack carries `p2pid` (no underscore) and the viewer's uid, not the stream id. The library tracks every
subscribe by `_id`, so the ack resolves it silently; a `failed` ack is logged at WARNING with its code and the
session stays joined (D31).

`unsubscribe` and `set_client_role` (CODE; not captured):
```json
{"_id": "c3d4e5", "_type": "unsubscribe", "_message": {"p2p_id": 1, "ortc": [], "stream_id": 1}}
{"_id": "e5f6a7", "_type": "set_client_role", "_message": {"role": "host", "level": 0, "client_ts": 1790716796000}}
```

- **Mammotion must never send `set_client_role`.** `set_client_role(host, level=0)` makes the mower leave the
  channel about 500 ms after video starts (project memory `project_agora_webrtc_fixes.md` §3). The captured
  sessions sent none and kept their streams (D6).
- **PetKit does send it,** straight after join success (`petkit/agora_websocket.py:354`); unverified here.

---

## 4. Keepalive, token renewal, leave

`ping` (CAPTURED, `gateway/real/ping.json`) and its reply (`gateway/real/ping_reply.json`):
```json
{"_id": "ab7c5d", "_type": "ping"}
{"_id": "ab7c5d", "_result": "success"}
```

- **The reply has no `_message`.** It is an `_id`-correlated success and nothing else.
- **Cadence.** The library pings every 3.0 s from the join result (first ping 3.003 s after it, intervals
  3.001–3.003 s); the SDK uses the same 3 s (SDK:31053-31057). Round trips were 295–337 ms, with outliers to
  1.5 s while media was starting. The last ping before a `leave` may go unanswered.
- **`ping_back`.** With `REPORT_STATS` the SDK follows with a fire-and-forget
  `{"_type": "ping_back", "_message": {"pingpongElapse": 42}}` (SDK; not sent by the library).
- **Correlation.** Each ping is tracked by `_id`, so its reply resolves silently (D31).
- **Timeout (SDK, SDK:31200-31226; never observed).** Each 3 s tick counts one more ping without a `success`
  reply; a reply resets the count. On the tick where it reaches `PING_PONG_TIME_OUT` (10: nine pings
  unanswered, 30 s after the last answered one) and no frame of any kind arrived for over
  `WEBSOCKET_TIMEOUT_MIN` (10 s), the SDK reconnects; with fresher frames it pings again.
- **Timeout (library, D31).** The same count and silence test; instead of reconnecting the session ends with
  `CloseReason.PING_TIMEOUT` and sends no `leave`. The host decides whether to start a new session. The fake
  gateway's `answer_pings=False` knob exercises it.

`renew_token` and the token events (CODE / SDK; not captured: every session was shorter than a minute against
tokens valid for an hour):
```json
{"_id": "a7b8c9", "_type": "renew_token", "_message": {"token": "rtc-token-not-real"}}
{"_type": "on_token_privilege_will_expire", "_message": {}}
{"_type": "on_token_privilege_did_expire", "_message": {}}
```
`will_expire` reportedly repeats about once a second in the pre-expiry window, so renewals are debounced to one
per 30 s (`agora_websocket.py:44-47`, D8). The payloads are unverified and unread.

`leave` (CAPTURED, `gateway/real/leave.json`), sent by `close()` after a successful join, then the socket closes:
```json
{"_id": "9cda11", "_type": "leave"}
```
No reply was captured before the socket closed. A session the gateway quit sends no `leave`.

**Mammotion FPV keep-alive.** Not Agora; it goes over MQTT or BLE. On a 4G link the mower's encoder stops
publishing unless it gets `SocMul{req_encode: MulSetEncode{encode: true}}` every 3 s (APK
`map/video/FPV4GVideoStateMannager.java:135`, gated by `is4GFPVLink`; `Luba-API/.../messages/video.py:48-50`).
Both mowers were on WiFi, so the capture has none; the HA keep-alive callback returned `False` and stopped.
The channel itself is opened and closed with `SocMul{set_video: MulSetVideo{position, vi_switch}}`.

### 4.1 Observed timings (CAPTURED, `sessions/*.json`)

| Step | Observed |
|---|---|
| AP request → response | 221–606 ms |
| AP response → `join_v3` sent | 0.9–3.1 s (the host waits for the browser's offer and candidates) |
| `join_v3` → result | 306–390 ms, twice 1.5–1.6 s, once 6.0 s (the second edge in `luba2_two_edges`) |
| result → `on_rtp_capability_change` | 0–7 ms |
| result → target's `on_user_online` | 3–764 ms |
| result → target's `on_add_video_stream` | 3 ms – 1.34 s |
| `on_add_video_stream` → `subscribe` sent | ≤ 2 ms |
| `subscribe` → ack | 295 ms – 2.0 s |
| result → `on_p2p_ok` | 0.96–4.35 s |
| result → first `ping` | 3.002–3.003 s, then every 3.0 s |
| `close()` → `leave` sent | 3 ms |

---

## 5. Error and notification frames

`on_notification` quit (CAPTURED, `gateway/real/on_notification_quit_repeat_join.json`):
```json
{"_message": {"action": "quit", "code": 2003, "detail": "ERR_REPEAT_JOIN", "option": ""}, "_type": "on_notification"}
```

`option` is present and empty (the SDK reads it for 2004 multi-IP recovery). A negative case from HA-Luba's test,
not captured: `{"_type": "on_notification", "_message": {"action": "warn", "code": 1}}`.

### 5.1 The 2003 eviction as observed (`sessions/luba2_left_then_right.json`)

1. 12:49:38.507 — `left` (target uid 1) sends `join_v3`; result 1.6 s later; it subscribes and pings for 26 s.
2. 12:50:03.606 — the host requests a fresh stream token and AP answer for `right` (target uid 2): same viewer
   uid, same channel, and the AP lists the same first edge `203.0.113.16:4721`.
3. 12:50:06.449 — `right` sends `join_v3` on that edge.
4. 12:50:06.789 — 340 ms later, `left`'s socket receives the quit above. This is **before** `right`'s own join
   result, which follows 50 ms later (12:50:06.839).
5. `left` ends as `GATEWAY_QUIT` and sends no `leave`; its next ping (due 12:50:07.17) is never sent. `right`
   is announced uid 2 at once and runs normally until the host closes it.

So the quit lands on the older session one round trip after the newer join reaches the gateway, ahead of the
newer join's result. D29 covers the case where it arrives while the older session is itself still joining.

### 5.2 Racing joins (`sessions/luba2_racing_pair.json`)

The host asked for both cameras one second apart; the two `join_v3`s left 31 ms apart (12:50:28.521 and .552).
Both got results (306 and 307 ms), both were announced both publishers, each subscribed its own target, both
reported `on_p2p_ok`, and both pinged for 31 s until the host closed them. No quit was sent. Each had its own AP
answer, and the two first edges were **different gateway edges** (`203.0.113.10:4712` and `203.0.113.13:3478`),
where the evicting pair in §5.1 shared one.

### 5.3 Staggered joins on two edges (`sessions/luba2_two_edges.json`, Q20 run 2)

The eviction is per gateway edge. The host put the second viewer on the AP's second edge with
`gateway_edge_offset=1` (D33); everything else matched §5.1: same viewer uid, same channel, the second join
long after the first completed.

1. 21:22:20.114 — `left` (target uid 1) asks the AP; its first edge is `203.0.113.22:4706`.
2. 21:22:22.738 — `left` sends `join_v3` on that edge; result 306 ms later, gateway host candidate
   `203.0.113.22:4706`. It subscribes uid 1 and pings every 3 s.
3. 21:22:37.401 — the host asks the AP for `right` (target uid 2). The answer lists `203.0.113.22:4706`,
   `203.0.113.11:4706`, `203.0.113.12:4704`.
4. 21:22:40.060 — `right` sends `join_v3` on the second edge, `203.0.113.11:4706`. The result takes 6.0 s
   (21:22:46.103); its host candidate is `203.0.113.11:4706`. `right` subscribes uid 2 and pings.
5. `left` gets no `on_notification`, no `on_user_offline` and no frame of any kind from the second join. In §5.1
   the quit came 340 ms after the newer `join_v3`. Both sessions answer every ping until the last recorded
   frame, 21:23:23.948, 43.9 s after `right`'s `join_v3`.

The recording ends there. The host log then shows both sockets closing 2 ms apart (21:23:24.412/.414), on both
edges at once, with no frame first. Nothing in it comes from the gateway. Each edge was named by the AP's
edge list and matches the host candidate in that session's join result; the capture logger does not log the
URL it dialled.

The uid cannot be varied instead: the AP refuses a uid the token was not minted for (`2010009`
`NO_AUTHORIZED`, §1.3, `ap/real/choose_server_rejected_no_authorized.json`), so no join is attempted.

### 5.4 Other error frames (SDK / CODE; not captured)

`error` (`agora_websocket.py:699-703`):
```json
{"_type": "error", "_message": {"error": "some error string"}}
```

A failed request, in the shape the SDK expects (SDK:30888-30897). The SDK reads `error_code || code`:
```json
{"_id": "a1b2c3", "_result": "failed", "_message": {"error_code": 2003, "error_str": "ERR_REPEAT_JOIN_CHANNEL"}}
```

`on_p2p_lost` (SDK event; Luba-API worktree copy of the old handler):
```json
{"_type": "on_p2p_lost", "_message": {"error_code": 1, "error_str": "stun timeout"}}
```
HA-Luba's handler read `error_code` / `error_str` from the top level; by the §2.1 envelope, confirmed for every
captured event, event fields live in `_message`. The library reads `_message` first (§5 addendum).

`on_user_banned` codes (SDK:30746-30757): 14 is UID_BANNED, 15 is IP_BANNED, 16 is CHANNEL_BANNED.
`on_user_license_banned` codes are 32769, 32771, 32773, 32774, 32778 and 32783 (table below).

**Gateway error codes and the action the SDK takes** (SDK:28260-28336, 29560-29760):

| Code | Name | Action |
|---|---|---|
| 4 | K_CERTIFICATE_INVALID | |
| 5 | K_CHANNEL_NAME_EMPTY | |
| 6 | K_CHANNEL_NOT_FOUND | |
| 7 | K_TICKET_INVALID | |
| 8 | K_CHANNEL_CONFLICTED | |
| 9 | K_SERVICE_NOT_READY | |
| 10 | K_SERVICE_TOO_HEAVY | |
| 14 / 15 / 16 | UID / IP / CHANNEL banned | close |
| 27 | DATASTREAM2_NOT_AVAILABLE | |
| 28 | K_AUTO_REBALANCE | recover notification |
| 30 | K_VOS_FALLBACK | |
| 103–108 | WARN_*: no available channel, lookup timeout or rejected, open timeout or rejected, request deferred | 108 → failed |
| 109 | ERR_DYNAMIC_KEY_TIMEOUT | failed |
| 110 | ERR_NO_AUTHORIZED | failed |
| 112 | ERR_NO_CHANNEL_AVAILABLE_CODE | failed |
| 115 | ERR_INTERNAL_ERROR | |
| 116 | ERR_NO_ACTIVE_STATUS | failed |
| 117 | ERR_INVALID_UID | failed |
| 118 | ERR_DYNAMIC_KEY_EXPIRED | failed |
| 119 / 120 | static/dynamic key mismatch | failed |
| 2001 | ERR_NO_VOS_AVAILABLE | |
| 2002 | ERR_JOIN_CHANNEL_TIMEOUT | |
| **2003** | **ERR_REPEAT_JOIN_CHANNEL** | **quit** |
| 2004 | ERR_JOIN_BY_MULTI_IP | recover (uses `_message.option`) |
| 2011 | ERR_NOT_JOINED | failed |
| 2012 | ERR_REPEAT_JOIN_REQUEST | quit |
| 2013 | ERR_INVALID_VENDOR_KEY | |
| 2014 | ERR_INVALID_CHANNEL_NAME | |
| 2015 | ERR_INVALID_STRINGUID | failed |
| 2016 | ERR_TOO_MANY_USERS | |
| 2017 | ERR_SET_CLIENT_ROLE_TIMEOUT | failed |
| 2018 | ERR_SET_CLIENT_ROLE_NO_PERMISSION | failed |
| 2019 | ERR_SET_CLIENT_ROLE_ALREADY_IN_USE | **success** |
| 2020 | ERR_PUBLISH_REQUEST_INVALID | failed |
| 2021 | ERR_SUBSCRIBE_REQUEST_INVALID | failed |
| 2022 | ERR_NOT_SUPPORTED_MESSAGE | failed |
| 2023 | ERR_ILLEAGAL_PLUGIN | failed |
| 2024 | ERR_REJOIN_TOKEN_INVALID | failed |
| 2025 | ERR_REJOIN_USER_NOT_JOINED | failed |
| 2027 | ERR_INVALID_OPTIONAL_INFO | quit |
| 2028 | ILLEGAL_AES_PASSWORD | failed |
| 2029 | ILLEGAL_CLIENT_ROLE_LEVEL | failed |
| 2031 | ERR_TOO_MANY_BROADCASTERS | failed (on join: close) |
| 2032 | ERR_TOO_MANY_SUBSCRIBERS | failed |
| 32769 / 32771 / 32773 / 32774 / 32778 / 32783 | LICENSE missing / expired / minutes exceeded / period invalid / multiple SDK service / illegal | quit |
| 9 / 9001 / 9002 | ERR_TEST_RECOVER / TRYNEXT / RETRY | recover |

A blank action cell means the SDK's table has no action for that code in the range read. The note on 2029 is
suggestive for Mammotion: `set_client_role` with level 0 may be exactly what triggers the mower quit described
in §3.3. That is unverified.

---

## 6. SDP samples

### 6.1 Offer from the HA frontend on Chrome (CAPTURED, `HA-Luba/compare_sdp.py:10-75`; ice-pwd redacted)

The source is truncated. The `m=video` line lists 42 PTs, but only the VP8/RTX `rtpmap` lines were kept. The
audio section is complete.

```sdp
v=0
o=- 1360638380926250848 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE 0 1
a=extmap-allow-mixed
a=msid-semantic: WMS
m=audio 9 UDP/TLS/RTP/SAVPF 111 63 9 0 8 13 110 126
c=IN IP4 0.0.0.0
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:nNCO
a=ice-pwd:ICEPWD_REDACTED_24chars0
a=ice-options:trickle
a=fingerprint:sha-256 69:56:A4:D2:A9:FD:9B:E5:09:63:1F:A1:96:59:04:28:E3:67:42:B2:CC:9C:26:79:15:FD:3C:0D:52:6F:7F:88
a=setup:actpass
a=mid:0
a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:3 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
a=extmap:4 urn:ietf:params:rtp-hdrext:sdes:mid
a=recvonly
a=rtcp-mux
a=rtcp-rsize
a=rtpmap:111 opus/48000/2
a=rtcp-fb:111 transport-cc
a=fmtp:111 minptime=10;useinbandfec=1
a=rtpmap:63 red/48000/2
a=fmtp:63 111/111
a=rtpmap:9 G722/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:13 CN/8000
a=rtpmap:110 telephone-event/48000
a=rtpmap:126 telephone-event/8000
m=video 9 UDP/TLS/RTP/SAVPF 96 97 98 99 100 101 35 36 37 38 103 104 107 108 109 114 115 116 117 118 39 40 41 42 43 44 45 46 47 48 119 120 121 122 49 50 51 52 123 124 125 53
c=IN IP4 0.0.0.0
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:nNCO
a=ice-pwd:ICEPWD_REDACTED_24chars0
a=ice-options:trickle
a=fingerprint:sha-256 69:56:A4:D2:A9:FD:9B:E5:09:63:1F:A1:96:59:04:28:E3:67:42:B2:CC:9C:26:79:15:FD:3C:0D:52:6F:7F:88
a=setup:actpass
a=mid:1
a=extmap:14 urn:ietf:params:rtp-hdrext:toffset
a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:13 urn:3gpp:video-orientation
a=extmap:3 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
a=extmap:5 http://www.webrtc.org/experiments/rtp-hdrext/playout-delay
a=extmap:6 http://www.webrtc.org/experiments/rtp-hdrext/video-content-type
a=extmap:7 http://www.webrtc.org/experiments/rtp-hdrext/video-timing
a=extmap:8 http://www.webrtc.org/experiments/rtp-hdrext/color-space
a=extmap:4 urn:ietf:params:rtp-hdrext:sdes:mid
a=extmap:10 urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id
a=extmap:11 urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id
a=recvonly
a=rtcp-mux
a=rtcp-rsize
a=rtpmap:96 VP8/90000
a=rtcp-fb:96 goog-remb
a=rtcp-fb:96 transport-cc
a=rtcp-fb:96 ccm fir
a=rtcp-fb:96 nack
a=rtcp-fb:96 nack pli
a=rtpmap:97 rtx/90000
a=fmtp:97 apt=96
```

The offer's shape is fixed by Home Assistant: 2 m-lines, audio at `mid:0` and video at `mid:1`, both `recvonly`,
`a=setup:actpass`, `a=extmap-allow-mixed`, and no `a=ssrc` lines.

### 6.2 Answer structure HA-Luba generates (CODE, `agora_websocket.py:1425-1700`)

- **Session header** (fixed):
  ```
  v=0
  o=- 0 0 IN IP4 127.0.0.1
  s=AgoraGateway
  t=0 0
  a=group:BUNDLE <offer mids>
  a=ice-lite
  [a=extmap-allow-mixed]
  a=msid-semantic: WMS
  ```
  `a=extmap-allow-mixed` appears only when the offer had it.
- **Media sections.** One per offer m-line, in offer order. The direction is the complement of the offer's:
  `recvonly` becomes `sendonly`.
- **Per-section attributes.** `c=IN IP4 127.0.0.1`, then the server's ice-ufrag and ice-pwd, then
  `a=fingerprint:<algorithm> <fp>`, then `a=setup` from the §2.5 table, then the offer's `mid`.
- **Candidates.** Every server candidate is written into every section.
- **Extensions.** An extension is listed only if its URI appears in both the server caps and the offer, and it
  uses **the offer's** extmap id. `sdes:mid` is always dropped.
- **Codecs.** The server's codecs are copied as they are: rtpmap, rtcp-fb, then fmtp with `;`-joined `k=v`.
  PTs are not remapped.
- **Validation.** The answer must have at least 2 m-lines or it is rejected (`:1715-1720`). If no ORTC arrives,
  a fallback SDP is used with random ICE and fingerprint values, opus PT 109 and VP8 PT 120 (`:1740-1790`).

### 6.3 Generated answer (GENERATED)

This is HA-Luba's `_generate_answer_sdp` run on the §6.1 offer and a server ORTC. The ORTC is the §2.5 fixture
with an opus and VP8 capability set, audio extensions {1 ssrc-audio-level, 4 mid} and video extensions
{2 abs-send-time, 3 twcc, 4 mid}.

```sdp
v=0
o=- 0 0 IN IP4 127.0.0.1
s=AgoraGateway
t=0 0
a=group:BUNDLE 0 1
a=ice-lite
a=extmap-allow-mixed
a=msid-semantic: WMS
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 127.0.0.1
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:KdDV
a=ice-pwd:ICEPWD_REDACTED_24chars1
a=ice-options:trickle
a=fingerprint:sha-256 BD:3E:08
a=setup:active
a=mid:0
a=candidate:udpcandidate 1 udp 2103266323 45.196.22.13 4707 typ host
a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=sendonly
a=rtcp-mux
a=rtcp-rsize
a=rtpmap:111 opus/48000/2
a=rtcp-fb:111 transport-cc
a=fmtp:111 minptime=10;useinbandfec=1
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 127.0.0.1
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:KdDV
a=ice-pwd:ICEPWD_REDACTED_24chars1
a=ice-options:trickle
a=fingerprint:sha-256 BD:3E:08
a=setup:active
a=mid:1
a=candidate:udpcandidate 1 udp 2103266323 45.196.22.13 4707 typ host
a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:3 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
a=sendonly
a=rtcp-mux
a=rtcp-rsize
a=rtpmap:96 VP8/90000
a=rtcp-fb:96 goog-remb
a=rtcp-fb:96 transport-cc
a=rtcp-fb:96 ccm fir
a=rtcp-fb:96 nack
a=rtcp-fb:96 nack pli
```

The `sdes:mid` extension (id 4) is absent from both sections even though both sides offered it. Use this as a
golden answer for the offer and ORTC pair. `tests_ha/test_agora_answer_sdp.py:74-120` shows how to parse it back
with `sdp_transform` and assert per section.

### 6.4 How the SDK writes remote SSRCs (SDK:44333-44385; the HA-Luba answer omits them)

For each remote `{ssrcId, rtx}`, the SDK writes:

```
a=ssrc:<ssrcId> label:track-XXXXXXXX
a=ssrc:<ssrcId> mslabel:<10 chars>
a=ssrc:<ssrcId> msid:<mslabel> track-XXXXXXXX
a=ssrc:<ssrcId> cname:<cname>
```

It writes the same lines for the rtx SSRC plus `a=ssrc-group:FID <ssrc> <rtx>`, and adds `SIM` when there is
more than one layer.

`agora_test.html:910-920` writes a variant with `msid:<cname> video-<ssrc>`, `mslabel:<cname>` and
`label:video-<ssrc>`.

---

## 7. Credential JSON per vendor

### 7.1 Mammotion `POST {MAMMOTION_API_DOMAIN}/device-server/v1/stream/token`

Request (CODE, `Luba-API/pymammotion/http/http.py:1116-1128`; APK `map/viewmodel/MapDeviceModel.java:74-83`):
```json
{"deviceId": "IOT_ID_REDACTED", "mode": 0, "cameraStates": [{"cameraState": 1}, {"cameraState": 0}, {"cameraState": 0}]}
```

The default is `[1,0,0]`. With `all_cameras` it is `[1,1,int(has_rear_camera)]`.

Response, current shape (CAPTURED, `HA-Luba/config/home-assistant.log:433-434`, 2026-09-30, redacted):
```json
{
  "code": 0,
  "msg": "Request success",
  "data": {
    "appid": "APPID_REDACTED",
    "license": "LICENSE_REDACTED",
    "openEncrypt": 0,
    "channelName": "IOT_ID_REDACTED",
    "areaCode": "AREA_CODE_EU",
    "uid": 12345678,
    "token": "TOKEN_REDACTED",
    "cameras": [
      {"cameraId": 1, "token": "TOKEN_REDACTED"},
      {"cameraId": 2, "token": "TOKEN_REDACTED"},
      {"cameraId": 3, "token": "TOKEN_REDACTED"}
    ],
    "availableTime": 3581
  }
}
```

- **Token equality.** The top-level `token` equals the `key` sent to the AP (§1.1). Each camera token is
  distinct. A fixture should keep the four tokens distinct and keep `data.token == ap.key`.
- **Keys absent in this capture.** `key` and `salt` were absent here and parse to `None`.
- **Encryption.** When `openEncrypt` is set, `key` and `salt` are present, and the APK then calls
  `enableEncryption(AES_256_GCM2)`. `encryptionKey = key` is used verbatim. `encryptionKdfSalt` is
  `base64decode(salt)` copied into a 32-byte array (APK `map/video/JoinChannelVideo.java:1264-1284`;
  `pymammotion/http/model/camera_stream.py:27-30`). **No encrypted-session sample exists locally.**
- **Web `join_v3` encryption fields (SDK, unverified on the wire).** The SDK sends `aes_mode`, `aes_secret` and
  `aes_salt` in `join_v3`, but `aes_secret` is not the key: it is `base64(RSA-OAEP-SHA256(embedded SPKI key,
  secret))`, with `aes_encrypt: true` behind a feature flag (`agoraRTC_N-4.24.3.js:63522-63558`, `:46356-46362`).
  The SDK error `ILLEGAL_AES_PASSWORD` (2028) is the gateway's answer to a bad one. `pyagorartc` sends none of these
  fields and logs a WARNING when `ChannelCredentials.encryption` is set (D20, Q17).

Response, older shape (CAPTURED, `HA-Luba/untitled`, a Python repr dump, redacted). It has **no `openEncrypt`,
`areaCode` or `availableTime`**, and the `channelName` is a device name padded with zeros:
```json
{"appid": "APPID_REDACTED", "license": "LICENSE_REDACTED", "channelName": "DEVICE_NAME_000000",
 "uid": 12345678, "token": "TOKEN_REDACTED",
 "cameras": [{"cameraId": 1, "token": "TOKEN_REDACTED"}, {"cameraId": 2, "token": "TOKEN_REDACTED"},
             {"cameraId": 3, "token": "TOKEN_REDACTED"}]}
```

`StreamSubscriptionResponse` currently requires `openEncrypt` and `areaCode`, so this older payload would fail to
parse. Useful as a negative or compatibility fixture.

- **Legacy APK shape.** `VideoResp{appid, cameras[{cameraId, token}], channelName, token, uid}`, with no license
  (APK `base_module/bean/resp/VideoResp.java:8-12`).
- **Current APK shape.** `StreamTokenRspon.data{appid, areaCode, availableTime, cameras, channelName, key,
  license, salt, token, uid}` (`base_module/entity/StreamTokenRspon.java:14-27`).

Fake-server shape (`Luba-API/tests/fakeserver/http_api.py:163-174`). It uses `cameraId: 0` and `areaCode: "EU"`,
unlike the live values:
```json
{"code": 0, "data": {"appid": "fake-agora-app", "openEncrypt": 0, "cameras": [{"cameraId": 0, "token": "fake-cam-token"}],
 "channelName": "fake-channel", "areaCode": "EU", "token": "fake-agora-token", "uid": 12345}}
```

`GET /device-server/v1/video-resource/{iot_id}` (fake-server shape, `http_api.py:176-187`; model in
`camera_stream.py:33-43`):
```json
{"id": "vr-1", "deviceId": "IOT_ID_REDACTED", "deviceName": "Luba-XXXX", "cycleType": 0,
 "usageYearMonth": "2026-08", "totalTime": 3600, "availableTime": 1800}
```

What the APK does with these credentials natively (`map/video/JoinChannelVideo.java:680-776`):
- `RtcEngineConfig`: `mChannelProfile = 1` (live broadcasting), `mAreaCode` from settings, and
  `setParameters({"rtc.report_app_scenario":{"appScenario":100,"serviceType":11,...}})`.
- `setClientRole(1)`, meaning BROADCASTER.
- `enableVideo()`, then `enableLocalVideo(false)` and `enableLocalAudio(false)`.
- `ChannelMediaOptions` with `autoSubscribeAudio`, `autoSubscribeVideo`, `publishMicrophoneTrack` and
  `publishCameraTrack` all true.
- `joinChannel(token, channelName, uid, options)`.
- On Yuka, `setRemoteVideoStreamType(camera.uid, 1)`, the low stream.
- `renewToken(newToken)` on expiry (`:830`, `:869`).

This is why the Web join uses `role: "host"`.

### 7.2 PetKit (`pypetkitapi.LiveFeed`; the package is not installed locally, so fields come from usage)

| Field | Used as | Source |
|---|---|---|
| `rtc_token` | `channel_key` / AP `key` | `petkit/agora_websocket.py:123,669`, `camera.py:628` |
| `channel_id` | `channel_name` / AP `cname` | `agora_websocket.py:670`, `camera.py:629` |
| `uid` | AP `uid` | `camera.py:630` |
| `app_rtm_user_id` | RTM sender, `x-agora-uid` | `agora_rtm.py:120` |
| `dev_rtm_user_id` | RTM `destination` | `agora_rtm.py:121` |
| `rtm_token` | RTM auth | `agora_rtm.py:122` |

The app id is hardcoded as `AGORA_APP_ID` (`petkit/const.py:14`), redacted here as `APPID_REDACTED`. The AP
request uses `service_ids [11, 26]`, the default area `"CN,GLOBAL"` and the default role 1, and PetKit keeps only
the first TURN server (`use_all_turn_servers=False`, `camera.py:625-638`).

---

## 8. RTM: PetKit peer messages over REST (CODE, `petkit/agora_rtm.py`; unverified: no RTM exchange has been captured, Mammotion does not use RTM)

```
POST https://{api.agora.io | api.sd-rtn.com}/dev/v2/project/{APPID}/rtm/users/{url-quoted app_rtm_user_id}/peer_messages[?wait_for_ack=true]
Content-Type: application/json
x-agora-token: TOKEN_REDACTED
x-agora-uid: <app_rtm_user_id>
Authorization: agora token=TOKEN_REDACTED
```

Body (`agora_rtm.py:208-218`). `payload` is a **JSON string** with compact separators, not a nested object.
```json
{"destination": "<dev_rtm_user_id>", "enable_offline_messaging": false, "enable_historical_messaging": false,
 "payload": "{\"cmd\":\"start_live\",\"payload\":{\"isSD\":0}}"}
```

Commands:

| Command | Payload | Call |
|---|---|---|
| `start_live` | `{"isSD":0}` | `wait_for_ack=true`, up to 5 tries 1 s apart |
| `live_heartbeat` | `{"isSD":0}` | every 0.5 s, no ack; stops after 10 consecutive failures |
| `stop_live` | none (the key is omitted) | no ack |
| `ptz_ctrl` | `{"type": 0\|1\|2, "ptz_dir": -1\|0\|1}` | with ack |

`ptz_ctrl` values: type 0 is a single step, 1 is continuous, 2 is flip. `ptz_dir` -1 is left, 0 is stop, 1 is
right.

Response (shape inferred from the parser at `agora_rtm.py:291-304`; no capture):
```json
{"result": "success", "code": "message_delivered"}
```

- **Success codes.** Success means `result == "success"` and `code` is in {`message_sent`,
  `message_delivered`}. `stop_live` also accepts `message_offline`.
- **Endpoint failover.** 404, 429 and 5xx move on to the next domain and path. Any other non-200 is a hard
  failure. The last good domain is tried first on the next call.
- **Mammotion** uses no RTM: the mower is driven over MQTT/BLE (`SocMul`, §4).

---

## Byte-level and ordering details the fixtures must preserve

1. **Service ids are not flags.** The request sends 11/18/20/26 and the response reports 4096/1048576/4194304/
   4194310. Match the `flag` exactly. `ap_response` in `join_v3` is the flag-4096 buffer with `cert` duplicated
   into `ticket` and the top-level `enter_ts` renamed `server_ts`.
2. **TURN credentials.** Username is `str(uid)` and password is `sha256(str(uid))` hex, 64 characters. The
   UDP/TCP URLs use port 3478, not the AP's 443. The TLS URL is `turns:{a-b-c-d}.edge.agora.io:443?transport=tcp`.
   The gateway TURN port is the gateway port + 30, with the token as password.
3. **Gateway fingerprints.** `detail["19"]` is `;`-separated (with a trailing `;`) and matched to
   `edges_services` by index. The captured gateway always sent its own fingerprint, equal to the connected edge's
   detail-19 entry; `pyagorartc` uses the AP's only when the gateway sends none (D26).
4. **Envelope correlation.** A frame with `_id` is a response and has `_result`; a frame without `_id` is an
   event. `_id` is 6 characters. On failure the code is in `_message.error_code`, falling back to
   `_message.code`.
5. **DTLS role.** Offer ORTC `role` is `"server"` (HA-Luba), `"client"` (PetKit) or absent (SDK). The server's
   `"client"`, `"server"` and `"auto"` map to answer `a=setup:active`, `passive` and `active` (the SDK answers
   `actpass` for `auto`, which RFC 5763 forbids in an answer; D5). Live Agora
   reported `"client"` in all six captured sessions, so the answer is `active`. The server side is `a=ice-lite`.
6. **MID extension.** `urn:ietf:params:rtp-hdrext:sdes:mid` is stripped from the answer only. Agora's edge
   hard-codes video at mid 2 internally, and HA's offer puts video at mid 1; with MID negotiated, Chrome drops
   every video RTP packet. With it stripped, BUNDLE demux falls back to payload type. The ORTC sent to Agora
   still lists it (entry 4).
7. **Extension ids** in the answer come from the offer, looked up by URI. The server's `entry` numbers are ignored.
8. **Payload types.** The server's PTs are copied into the answer unchanged, and the video PT is the only demux
   key. `on_add_video_stream.pt == 0` means no codec match (a browser without H265 decode); the capture shows
   `pt: 49` (H265). The captured join result lists an `rtx` PT for every video codec, so `subscribe.rtx` is true;
   it must be false whenever no `rtx` codec is present. `codec` must be identical in `join_v3` and `subscribe`:
   `vp8` for Mammotion, `h264` for PetKit.
9. **SSRCs.** They come from `on_add_video_stream.ssrcId`, and `rtxSsrcId` when present, and go back in
   `subscribe.ssrcId`. They are allocated per viewer session in announcement order (40000/40001, then
   40002/40003), not per publisher. HA-Luba's answer writes no `a=ssrc` lines. The captured gateway always sent
   `on_user_online` before `on_add_video_stream`; the session tolerates either order.
10. **msid.** Project memory records the fix that made the working stream's msid stream id `"1"`, the mower's
    uid, with per-session UUID track ids. HA-Luba still initialises `_msid_stream_id = 1` and the UUIDs
    (`agora_websocket.py:174-177, 235-238`), but the current `_generate_answer_sdp` emits **no `a=msid` line**.
    A fixture asserting msid would fail against current code. Confirm the intended behaviour before pinning it.
11. **Mower uids.** They are 1, 2 and 3, one per camera slot. Each camera needs its own freshly minted stream
    token. A later join with the same viewer uid on the same edge makes the gateway quit the first session with
    `on_notification {action: "quit", code: 2003, …}` (§5.1). The check is per edge: viewers with one uid on
    different edges both survive, racing (§5.2) or staggered (§5.3, D33).
12. **Timers.**
    - WebSocket ping every 3 s.
    - Mammotion 4G `MulSetEncode` every 3 s.
    - PetKit RTM heartbeat every 0.5 s.
    - Renew-token debounce 30 s.
    - Peer-rejoin debounce 2 s, recovery cooldown 15 s, at most 5 attempts, count reset after 600 s.
    - Join-response timeout 15 s; per-edge connect timeout 10 s.
13. **Ordering on join.** Open the WebSocket, send `join_v3`, and wait for `_result: success` with `ortc`. Then
    generate the answer and start the ping loop. `on_rtp_capability_change` follows at once, then one
    `on_user_online` + `on_add_video_stream` pair per publisher already in the channel (none is listed in the
    result), then `subscribe`. For Mammotion, send no `set_client_role`.

### §8 addendum: library behaviour on RTM acks

A `failed` result is rejected even when its `code` is in the accepted set;
`result` and `code` are compared case-insensitively; a non-rotating non-200
raises `RtmError(status=)`; after the last host the error carries the last
status, or none if that host was unreachable (Q14).

### §5 addendum: `on_p2p_lost`

`_message` is authoritative; the top level is read only for a field missing
from `_message` (the shipped handler read the top level only, which was a bug).

### §1 addendum: library behaviour on AP responses

Fingerprints in detail 19 are matched to edges by index as sent; an empty `;`
entry keeps its slot. TURN credentials are always uid-derived; the deprecated
`TurnCredentialStrategy.DETAIL_FIRST` gives the same pair and a
`DeprecationWarning`, because detail 8 is the `vid` in both blocks, not a
username (the PetKit copy sent it; D32). Detail `6` is
sent last, after 11/17/22 (the SDK sends it first; order has not been shown
to matter).

### §2.5 addendum

Fixtures redact the gateway candidate address to `198.51.100.13` (RFC 5737).

### §3.3 addendum: subscribe retry

With `subscribe_retry_attempts > 0` the session waits `subscribe_retry_delay_s`
for the subscribe ack and re-sends when none arrived or it failed, up to that
many times; with the default 0 it sends once. The last attempt's ack is
tracked without a timer: a success resolves it, a failure logs a WARNING (D31). A subscribe
goes out once both the stream announcement and the publisher's
`on_user_online` have been seen; publishers listed in the join payload
count as online.

### §7 addendum: the token request starts the publisher (Mammotion, observed 2026-10-01)

On current Mammotion firmware the cloud `stream/token` call with `cameraStates`
is what makes the mower publish: a join made with a cached, still-valid token
succeeded but no stream was ever announced, while every join preceded by a
fresh token request saw the publisher within a second. Re-using one token for
two viewer sessions does not avoid `2003 ERR_REPEAT_JOIN`; the eviction is
per uid. A `target_uid=2` session on a Luba 3 also saw the publisher leave
about 50 s after the token was minted, so the mower appears to stop
publishing when nothing subscribes.
