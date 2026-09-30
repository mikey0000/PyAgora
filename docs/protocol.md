# Protocol

> Provenance: every sample below names its source. Server-to-client gateway frames are **reconstructed** from the Web SDK's parser and our own handlers, not captured; `backlog.md` tracks recording real ones (`Luba-API/scripts/frida/agora-ws.js` or the HA logger at DEBUG). Anything marked 'from SDK knowledge, unverified' is exactly that.


Collected read-only from HA-Luba, Luba-API, the PetKit integration, the Mammotion 2.3.8.201 APK tree and the
Agora Web SDK bundle that HA-Luba keeps (`/home/michael/git/HA-Luba/agoraRTC_N-4.24.3.js`, 84 200 lines,
untracked). The 2.3.18.21 tree is still packed (`.xapk` / `.apk` only), so it gave nothing.

## Provenance labels

| Label | Meaning |
|---|---|
| **CAPTURED** | Copied from a real run log, then redacted. |
| **CODE** | The exact shape our Python sends or parses, taken from source. The server accepts it, but this is not a capture. |
| **SDK** | Read directly from `agoraRTC_N-4.24.3.js` at the line cited. |
| **SDK knowledge, unverified** | From general knowledge of the Agora Web SDK. No local source confirms it. |
| **GENERATED** | Output of HA-Luba's own code on real inputs, produced for this document (see §6.3). |

**No local source has a raw capture of any server-to-client WebSocket frame.** That covers the join response
with `ortc`, `on_add_video_stream`, `on_user_online` and the rest. Home Assistant's DEBUG log recorded only the
REST legs: `stream/token`, `choose_server`, and the parse summaries. The Frida tap
`Luba-API/scripts/frida/agora-ws.js` is built to capture these frames, but no `agora-*.log` output exists on
disk. For each server frame below, the shape is put together from three things: the fields our handlers read,
a test fixture trimmed from a real response, and the SDK's parser. Recording one Frida session, or one HA DEBUG
session with `custom_components.mammotion.agora_websocket` at DEBUG (it logs `Received Agora message: %s` at
`agora_websocket.py:360`), would make all of §2–§5 CAPTURED.

## Redaction rules applied

- `APPID_REDACTED`: Agora app id. Mammotion's is a 32-hex string; PetKit's is hardcoded in `petkit/const.py:14`.
- `TOKEN_REDACTED`: Agora AccessToken2. Real values start `007eJx` (version `007`, then base64 of a zlib
  stream) and are 140–160 characters. Keep the `007e` prefix in fixtures if a parser checks it.
- `LICENSE_REDACTED`: 32 upper-case hex characters.
- `IOT_ID_REDACTED`: the Mammotion `channelName`, which is the device iot_id (26 characters).
- `UID_REDACTED`: the viewer's Agora uid, an 8-digit int in the captures. In fixtures, use an int.
- `SHA256_OF_UID_REDACTED`: the TURN password, `sha256(str(uid)).hexdigest()`, 64 hex characters.
- `ICEPWD_REDACTED_24chars0/1`: ICE passwords, replaced with same-length placeholders.
- Left as-is: Agora edge IPs (public infrastructure), DTLS fingerprints (public certificate hashes), ICE
  ufrags, opid, sid and timestamps.

---

## 1. AP discovery (`choose_server`, URI 22) and `update_ticket` (URI 28)

**Endpoint** (CODE, `HA-Luba/custom_components/mammotion/agora_api.py:503-513, 834-848`):
`POST https://webrtc2-ap-web-{1..4}.agora.io/api/v2/transpond/webrtc?v=2`. The backups are `-5` and `-6`.
The body is `multipart/form-data` with one field, `request`, holding the JSON below. With a cloud proxy the URL
becomes `https://{proxy}/ap/?url={domain}/api/v2/transpond/webrtc?v=2`.

### 1.1 Request (CAPTURED, `HA-Luba/config/home-assistant.log:436`, 2026-09-30)

```json
{
  "appid": "APPID_REDACTED",
  "client_ts": 1790716794173,
  "opid": 999972753885,
  "sid": "152075528",
  "request_bodies": [
    {
      "uri": 22,
      "buffer": {
        "cname": "IOT_ID_REDACTED",
        "detail": {"11": "CN,GLOBAL", "17": "1", "22": "CN,GLOBAL"},
        "key": "TOKEN_REDACTED",
        "service_ids": [11, 26],
        "uid": 12345678
      }
    }
  ]
}
```

The same request appears in `home-assistant.log.1:397` (2026-09-29) with `opid 992571224445` and
`sid "2079850297"`.

How the request is built (CODE, `agora_api.py:731-772`):
- `opid` is a random int below 10¹².
- `sid` is a random int below 2³¹, sent as a string.
- In `detail`, keys `11` and `22` carry the area code and `17` carries the role (`"1"` host, `"2"` audience).
- The SDK also sends `"6": stringUid` and `"12": "1"` (new-token flag). HA-Luba omits both. PetKit sends `"6"`
  (`petkit/agora_api.py:370-376`). `"26": "RTM2"` is only sent when RTM2 is on.
- `update_ticket` (URI 28) has the same envelope plus `buffer.edges_services: [{ip, port}, ...]`
  (`agora_api.py:606-673`).
- When the SDK knows a multi-IP gateway, it writes
  `detail["5"] = JSON.stringify({vocs_ip:[...], vos_ip:[...]})` (SDK, `agoraRTC_N-4.24.3.js:48512`).

### 1.2 Service id to response `flag` mapping (SDK `agoraRTC_N-4.24.3.js:34376-34379`, `43683-43700`)

| Request `service_ids` entry | Name | Response `buffer.flag` | Hex | Carries |
|---|---|---|---|---|
| 11 | CHOOSE_SERVER | 4096 | `0x001000` | WebSocket gateway edges |
| 18 | CLOUD_PROXY | 1048576 | `0x100000` | proxy3 edges |
| 20 | CLOUD_PROXY_5 | 4194304 | `0x400000` | proxy5 edges |
| 26 | CLOUD_PROXY_FALLBACK | 4194310 | `0x400006` | TURN edges |

The flags behave like tags rather than a bitmask: the SDK matches `buffer.flag === value` exactly, and
4194310 = `0x400000 | 0x6`. A service id outside this table raises "multi unlibs response transformer get
unknown service id". Some older docs in HA-Luba (`AGORA_API_ANALYSIS.md:41`, `AGORA_STUN_TURN_ANALYSIS.md:287`)
send the flag values themselves in `service_ids`. That is wrong: the request takes the ids (11/18/20/26).

### 1.3 Response (shape: CODE and SDK. Values: CAPTURED as parse summaries only)

The run behind §1.1 logged this (`home-assistant.log:453-460`):
```
Agora API response body count: 2
Parsing response flag=4096, uid=<UID>, edges_count=3
Parsing response flag=4194310, uid=<UID>, edges_count=3
Processing TURN address: ip=128.1.186.243, port=443, username=<UID>, cred_len=64
```
The previous day's run gave the TURN edge `129.227.71.165:443` (`home-assistant.log.1:412`). The full response
body was not logged. Here is a fixture with the SDK's field set (`agoraRTC_N-4.24.3.js:43710-43733`) filled
with the captured values:

```json
{
  "enter_ts": 1790716795180,
  "opid": 999972753885,
  "detail": {},
  "response_body": [
    {
      "uri": 23,
      "buffer": {
        "code": 0,
        "flag": 4096,
        "uid": 12345678,
        "cid": 123456789,
        "cname": "IOT_ID_REDACTED",
        "cert": "TICKET_REDACTED",
        "detail": {
          "1": "UNI_LBS_IP",
          "8": "VID",
          "19": "FP_A;FP_B;FP_C",
          "23": "EU",
          "502": "CS_IP",
          "candidate": "1.2.3.4:4707"
        },
        "edges_services": [
          {"ip": "1.2.3.4", "port": 4713},
          {"ip": "5.6.7.8", "port": 4713},
          {"ip": "9.10.11.12", "port": 4713}
        ]
      }
    },
    {
      "uri": 23,
      "buffer": {
        "code": 0,
        "flag": 4194310,
        "uid": 12345678,
        "cid": 123456789,
        "cname": "IOT_ID_REDACTED",
        "cert": "TICKET_REDACTED",
        "detail": {},
        "edges_services": [
          {"ip": "128.1.186.243", "port": 443},
          {"ip": "129.227.71.165", "port": 443},
          {"ip": "9.10.11.12", "port": 443}
        ]
      }
    }
  ]
}
```

In the fixture above, `uri: 23`, the gateway port 4713 and the gateway IPs are illustrative. They come from
`HA-Luba/AGORA_API_ANALYSIS.md:55` and `AGORA_EDGE_SERVER_SELECTION.md:30-43`, which are written from reading
the SDK, not from captures.

What each `detail` key means, as the SDK reads it:

| Key | Meaning | Source |
|---|---|---|
| `1` | `uni_lbs_ip` | SDK:43632 |
| `8` | `vid` | SDK:43631, 43455 |
| `18` | per-address IPv6, `;`-separated | SDK:43604 |
| `19` | per-address DTLS fingerprint, `;`-separated, matched to addresses by index; each `sha-256 AA:BB…` or a bare value (both hosts parsed both; the fake sends the prefixed form) | SDK:48698-48704, `agora_api.py:172-177` |
| `23` | area | SDK:48689 |
| `38` | cross-region tag | SDK:48512 |
| `502` | `csIp`, used in error reports | SDK:43634 |
| `candidate` | `"ip:port"`, the AP-suggested gateway (`apGatewayAddress`) | SDK:43611-43620 |

- The top-level `detail` is merged over each buffer's `detail` (SDK:43726).
- A buffer with `code != 0` makes HA-Luba raise (`agora_api.py:149-150`).
- `code == 0` with empty `edges_services` makes the SDK raise `CAN_NOT_GET_GATEWAY_SERVER` (SDK:48676-48681).

### 1.4 What is derived from the AP response

**WebSocket URL** (CODE, `agora_websocket.py:268`): `wss://{ip with '.'→'-'}.edge.agora.io:{port}`, taken from
the flag-4096 edges in order. The SDK can also use an `edge.sd-rtn.com` dual domain and a port-443-only mode
(`JOIN_GATEWAY_USE_DUAL_DOMAIN` / `USE_443PORT_ONLY`, SDK:30784-30785). The exact host pattern for those modes
is SDK knowledge, unverified.

**`ap_response` inside `join_v3`** (CODE, `agora_api.py:453-492`): the flag-4096 buffer reshaped.

```json
{"code": 0, "server_ts": 1790716795180, "uid": 12345678, "cid": 123456789, "cname": "IOT_ID_REDACTED",
 "detail": {"19": "FP_A;FP_B;FP_C"}, "flag": 4096, "opid": 999972753885,
 "cert": "TICKET_REDACTED", "ticket": "TICKET_REDACTED"}
```

**ICE servers handed to Home Assistant or the browser** (CAPTURED, `home-assistant.log:458-461`, redacted).
Each TURN edge expands into three entries. The UDP and TCP entries ignore the AP port and use 3478; the TLS entry
uses the dashed hostname on 443 (`agora_api.py:298-324`).

```json
[
  {"urls": "turn:128.1.186.243:3478?transport=udp", "username": "UID_REDACTED", "credential": "SHA256_OF_UID_REDACTED"},
  {"urls": "turn:128.1.186.243:3478?transport=tcp", "username": "UID_REDACTED", "credential": "SHA256_OF_UID_REDACTED"},
  {"urls": "turns:128-1-186-243.edge.agora.io:443?transport=tcp", "username": "UID_REDACTED", "credential": "SHA256_OF_UID_REDACTED"}
]
```

- The credential is `hashlib.sha256(str(uid).encode()).hexdigest()` (`agora_api.py:39-55`). This matches the
  SDK's `ENCRYPT_PROXY_USERNAME_AND_PSW` path, which applies in secure contexts only (SDK:43740-43755).
- Test vector: the log shows `cred_len=64`. Assert `derive_password(12345678) == hashlib.sha256(b"12345678").hexdigest()`.

**SDK-style `turnServer` object** (CODE, `agora_api.py:350-405`; example in `AGORA_EDGE_SERVER_SELECTION.md:66-89`,
redacted):

```json
{
  "mode": "manual",
  "servers": [
    {"turnServerURL": "128.1.186.243", "tcpport": 443, "udpport": 443, "username": "UID_REDACTED",
     "password": "SHA256_OF_UID_REDACTED", "forceturn": false, "security": true}
  ],
  "serversFromGateway": [
    {"username": "UID_REDACTED", "password": "TOKEN_REDACTED", "turnServerURL": "1.2.3.4",
     "tcpport": 4743, "udpport": 4743, "forceturn": false}
  ]
}
```

For `serversFromGateway`, the port is the gateway port + 30 and the password is the channel token itself. That
entry has no `security` key.

---

## 2. WebSocket `join_v3`

### 2.1 Frame envelope (SDK `agoraRTC_N-4.24.3.js:30731-30745, 30800-30806, 30956-30963`)

| Direction | Shape |
|---|---|
| Client request | `{"_id": "<6 chars>", "_type": "<verb>", "_message": {...}}`. The SDK makes the 6-char id with `qO(6,"")`. HA-Luba uses `secrets.token_hex(3)`. |
| Client fire-and-forget (`upload` / `send`) | `{"_type": "...", "_message": {...}}`, no `_id`. |
| Server response | `{"_id": "<echoed>", "_result": "success" \| "failed", "_message": {...}}`. It has an `_id`, and in the SDK's dispatch a message with an `_id` is treated as a response even if it also carries `_type`. |
| Server event | `{"_type": "<event>", "_message": {...}}`, no `_id`. |
| Binary frame | Emitted as `ON_BINARY_DATA`. Not otherwise used by us. |

On failure the SDK reads `Number(_message.error_code || _message.code)` and `_message.error_str`
(SDK:30888-30897). The error table is in §5.

### 2.2 Client verbs (SDK:28390-28432)

`ping`, `ping_back`, `join_v3`, `rejoin_v3`, `leave`, `set_client_role`, `publish`, `publish_datastream`,
`unpublish`, `unpublish_datastream`, `subscribe`, `pre_subscribe`, `subscribe_datastream`, `subscribe_streams`,
`unsubscribe`, `unsubscribe_datastream`, `unsubscribe_streams`, `subscribe_change`, `traffic_stats`,
`renew_token`, `set_dual_stream_mode`, `switch_video_stream`, `default_video_stream`, `set_fallback_option`,
`configure`, `gateway_info`, `control`, `send_metadata`, `data_stream`, `pick_svc_layer`, `restart_ice`,
`connect_pc`, `set_video_profile`, `set_parameter`, `set_rtm2_flag`, `downgrade_codec`.

Upload-only types (SDK:28437-28442): `wrtc_stats`, `ws_inflate_data_length`, `denoiser_stats`,
`extension_usage_stats`.

### 2.3 `join_v3` request (CODE, `HA-Luba/custom_components/mammotion/agora_websocket.py:958-1019`)

```json
{
  "_id": "a1b2c3",
  "_type": "join_v3",
  "_message": {
    "p2p_id": 1,
    "session_id": "0F2C9E6B4A1D4E8F9C3B2A1D0E9F8A7B",
    "app_id": "APPID_REDACTED",
    "channel_key": "TOKEN_REDACTED",
    "channel_name": "IOT_ID_REDACTED",
    "sdk_version": "4.24.3",
    "browser": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "process_id": "process-1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f",
    "mode": "live",
    "codec": "vp8",
    "role": "host",
    "has_changed_gateway": false,
    "ap_response": {"code": 0, "server_ts": 1790716795180, "uid": 12345678, "cid": 123456789,
                    "cname": "IOT_ID_REDACTED", "detail": {}, "flag": 4096, "opid": 999972753885,
                    "cert": "TICKET_REDACTED", "ticket": "TICKET_REDACTED"},
    "extend": "",
    "details": {},
    "features": {"rejoin": true},
    "attributes": {
      "enableAudioMetadata": false, "enableAudioPts": false, "enableNetworkQualityProbe": false,
      "enablePublishedUserList": true, "enableUserList": false, "maxSubscription": 50,
      "enableUserLicenseCheck": true, "enableRTX": true, "enableInstantVideo": false,
      "enableDataStream2": false, "enableAutFeedback": true, "enableUserAutoRebalanceCheck": true,
      "enableXR": true, "enableLossbasedBwe": true, "enableAutCC": true, "enablePreallocPC": true,
      "enablePubTWCC": false, "enableSubTWCC": true, "enablePubRTX": true, "enableSubRTX": true,
      "enableVosFallback": false, "enableQualityFallback": false, "enableDualStreamFlag": false
    },
    "join_ts": 1790716795300,
    "ortc": "<see §2.4>"
  }
}
```

The implementations differ in ways a fixture should cover:

- **`attributes` nesting.**
  - The SDK sends `attributes: {userAttributes: {...}}` (SDK:46262).
  - `agora_test.html:1485` and PetKit (`petkit/agora_websocket.py:688`) also nest.
  - HA-Luba's Python sends the keys flat, and the gateway accepts that.
  - Test both shapes.
- **`details`.** The SDK sends `{"6": stringUid, "cservice_map": "1" | "2" | undefined}` (SDK:46240-46248).
  Both Python ports send `{}`.
- **`sdk_version` / `codec`.**

  | Client | `sdk_version` | `codec` | `role` |
  |---|---|---|---|
  | HA-Luba | `"4.24.3"` | `"vp8"` | `"host"` |
  | `agora_test.html` | `"4.24.2"` | `"vp8"` | `"host"` |
  | PetKit | `"4.24.0"` | `"h264"` | `"host"` |

  `codec` is the client's codec spec, not the publisher's codec. The SDK uses `this.spec.codec` in both
  `join_v3` and `subscribe`, so the two must match (HA-Luba commit `8cc8a51`, `agora_websocket.py:115-119`).
- **`mode`.** The SDK sends `"p2p"` when `useP2P` is set, otherwise the spec mode, which is `"live"` here.
- **SDK-only fields.** `optionalInfo` and `appScenario`.

### 2.4 Client `ortc` inside `join_v3` (GENERATED from HA-Luba's `parse_offer_to_ortc`, `agora_sdp.py:209-362`, on the Chrome offer in §6.1)

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
         "fmtp": {"parameters": {"minptime": "10", "useinbandfec": "1"}}},
        {"payloadType": 63, "rtpMap": {"encodingName": "red", "clockRate": 48000, "encodingParameters": 2},
         "rtcpFeedbacks": [{"type": "rrtr"}], "fmtp": {"parameters": {"111/111": null}}}
      ],
      "audioExtensions": [
        {"entry": 1, "extensionName": "urn:ietf:params:rtp-hdrext:ssrc-audio-level"},
        {"entry": 2, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"},
        {"entry": 3, "extensionName": "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"},
        {"entry": 4, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid"}
      ],
      "videoCodecs": [
        {"payloadType": 96, "rtpMap": {"encodingName": "VP8", "clockRate": 90000},
         "rtcpFeedbacks": [{"type": "goog-remb"}, {"type": "transport-cc"}, {"type": "ccm", "parameter": "fir"},
                           {"type": "nack"}, {"type": "nack", "parameter": "pli"}, {"type": "rrtr"}],
         "fmtp": {"parameters": {}}},
        {"payloadType": 97, "rtpMap": {"encodingName": "rtx", "clockRate": 90000},
         "rtcpFeedbacks": [{"type": "rrtr"}], "fmtp": {"parameters": {"apt": "96"}}}
      ],
      "videoExtensions": [
        {"entry": 14, "extensionName": "urn:ietf:params:rtp-hdrext:toffset"},
        {"entry": 2, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"},
        {"entry": 13, "extensionName": "urn:3gpp:video-orientation"},
        {"entry": 3, "extensionName": "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"},
        {"entry": 5, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay"},
        {"entry": 6, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/video-content-type"},
        {"entry": 7, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/video-timing"},
        {"entry": 8, "extensionName": "http://www.webrtc.org/experiments/rtp-hdrext/color-space"},
        {"entry": 4, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:mid"},
        {"entry": 10, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id"},
        {"entry": 11, "extensionName": "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id"}
      ]
    }
  },
  "version": "2"
}
```

The audio list is trimmed to 2 of 8 codecs. The other six are G722/9, PCMU/0, PCMA/8, CN/13,
telephone-event/110 and telephone-event/126, each with `rtcpFeedbacks: [{"type":"rrtr"}]`.

Details in this structure that matter:

- **`fmtp` flag keys.** A key with no value becomes `null`, as in RED's `"111/111": null`. The SDK does the same
  (`params[k] = v ? v.trim() : null`).
- **`rrtr`.** An `{"type":"rrtr"}` feedback is appended to every codec that lacks one.
- **`dtlsParameters.role`.** Each implementation does something different:
  - HA-Luba sends `"server"`, meaning "the browser is DTLS server". This is the fix for a DTLS deadlock.
  - PetKit sends `"client"` (`petkit/agora_sdp.py:167`).
  - The SDK's own `f2()` sends **no `role`**, only `fingerprints` (SDK:44146-44180).

  Treat `role` as optional on input.
- **Send/receive buckets.** The SDK splits codecs by `can_send`: H265, VP9 profile 1/3 and AV1 profile 1 go to
  `recv`, everything else to `sendrecv` (`agora_sdp.py:216-231`). The Chrome offer here has only VP8/RTX, so
  `send` and `recv` are empty.
- **MID extension.** The `sdes:mid` extension is **not** stripped from the ORTC that goes to Agora. It is
  stripped only from the answer SDP (§6).

### 2.5 `join_v3` success response (shape: test fixture trimmed from a real response, plus SDK)

Base fixture: `HA-Luba/tests_ha/test_agora_answer_sdp.py:25-65`, whose docstring says it is "a join response
trimmed to what the answer is built from". The envelope and extra keys come from `agora_websocket.py:563-580`
and SDK:31050-31052, 44221-44256.

```json
{
  "_id": "a1b2c3",
  "_result": "success",
  "_message": {
    "uid": 12345678,
    "cid": 123456789,
    "vid": 987654,
    "cname": "IOT_ID_REDACTED",
    "rejoin_token": "REJOIN_TOKEN_REDACTED",
    "ortc": {
      "cname": "o/i14u9pJrxRKAsu",
      "iceParameters": {
        "iceUfrag": "KdDV",
        "icePwd": "ICEPWD_REDACTED_24chars1",
        "candidates": [
          {"foundation": "udpcandidate", "ip": "45.196.22.13", "port": 4707,
           "priority": 2103266323, "protocol": "udp", "type": "host"}
        ]
      },
      "dtlsParameters": {
        "role": "client",
        "fingerprints": [{"algorithm": "sha-256", "fingerprint": "BD:3E:08"}]
      },
      "rtpCapabilities": {
        "sendrecv": {
          "audioCodecs": [{"payloadType": 111, "rtpMap": {"encodingName": "opus", "clockRate": 48000}}],
          "videoCodecs": [{"payloadType": 102, "rtpMap": {"encodingName": "H264", "clockRate": 90000}}],
          "audioExtensions": [],
          "videoExtensions": []
        }
      }
    }
  }
}
```

- **Where the `ortc` sub-keys come from.** `cname`, `iceUfrag`, `candidates[0]` and the `role: "client"` value
  are from the real-response test. The fingerprint `"BD:3E:08"` is a deliberately truncated stub. Real
  fingerprints are 32 colon-separated bytes.
- **Fingerprint key names.** The server's fingerprints use `algorithm` + `fingerprint` (SDK:44232-44235). The
  client's offer ORTC uses `hashFunction` + `fingerprint`. HA-Luba's fingerprint injection
  (`agora_websocket.py:593-622`) writes `hashFunction` into the server list, so parsers should accept both keys.
- **DTLS role → answer `a=setup`.**

  | Server `role` | `a=setup` | Source |
  |---|---|---|
  | `server` | `passive` | SDK:44242-44251, `agora_websocket.py:1538-1539` |
  | `client` | `active` | same |
  | `auto` | `actpass` (SDK); `pyagora` answers `active` (RFC 5763 §5, D5) | same |

  Agora reports `"client"` in practice (commit `8cc8a51`, `test_agora_answer_sdp.py:117`).
- **Where the codecs sit.** `rtpCapabilities` may hold its codecs under `sendrecv`, `recv`, `send`, or flat at
  the top level. Pick the first non-empty one, in that order (`agora_websocket.py:1411-1423`;
  `sdp/offer.py::negotiated_caps`).
- **No RTX.** The live gateway's join response lists **no `rtx` payload types** (commit `8cc8a51`). The
  subscribe `rtx` flag is set to whether any `videoCodecs[].rtpMap.encodingName` equals `rtx`
  (`agora_websocket.py:633-636`).
- **Filling in candidates.** If `iceParameters.candidates` is empty, the SDK makes a candidate from the gateway
  address it connected to: `{foundation: "udpcandidate", componentId: "1", transport: "udp",
  priority: "2103266323", connectionAddress: ip, port, type: "host"}`, plus a second one for `ip6` when present
  (SDK:44219-44330). This is why every captured candidate has foundation `udpcandidate` and priority 2103266323.
- **Pre-subscribe SSRCs.** When pre-subscribe is on, the SDK also reads `attributes.userAttributes.preSubSsrcs`,
  an array of `{v, v_rtx, a}` (SDK:44258-44289, 67326).
- **Streams listed in the join response.** PetKit scans the whole join response for objects that have `uid` and
  `ssrcId` plus a video marker (`video: true`, `stream_type: "video"`, `rtxSsrcId`, or `codec` in
  h264/h265/video), so it can subscribe to publishers that were already there (`petkit/agora_websocket.py:598-612`).
  The exact key the server uses for them is unverified.

### 2.6 `rejoin_v3` (SDK:31061-31128)

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

The test page also handles `on_subscribe_success` with `{stream_id}` and `on_mute`/`on_unmute` with
`{uid, audio, video}` (`agora_test.html:1743-1760`). The SDK's enum does not include these names, so they are
unverified.

### 3.2 Event fixtures (CODE: the fields our handlers read. Values illustrative unless marked)

`on_user_online` (`agora_websocket.py:737-763`; `agora_test.html:1634`):
```json
{"_type": "on_user_online", "_message": {"uid": 1}}
```

`on_add_video_stream` (`agora_websocket.py:765-823`):
```json
{"_type": "on_add_video_stream", "_message": {
  "uid": 1, "uint_id": 1, "video": true, "ssrcId": 44444444, "rtxSsrcId": 44444445,
  "cname": "o/i14u9pJrxRKAsu", "codec": "h265", "pt": 0
}}
```

- **`pt` is the payload type the gateway will relay the stream on.** `pt: 0` means it found no usable codec in
  the browser offer. The mower publishes **H265**, so a browser without HEVC decode gets `pt=0`
  (commit `8cc8a51`, `agora_websocket.py:782-793`). Build fixtures for both `pt: 0` and a real PT.
- **This event can arrive before `on_user_online`.** Subscribe only when both have been seen, whichever arrives
  second (`agora_websocket.py:737-823`; `agora_test.html:1634-1700`).
- **Mower uids.** The Mammotion mower publishes each camera slot n (0-based) as Agora uid n+1, so 1 is front/left,
  2 is front/right and 3 is rear, on Yuka only (`Luba-API/pymammotion/http/http.py:1110-1116`,
  `tests/unit/http/test_http_stream_subscription.py:37`).
- **Filter by target uid.** Each camera handler ignores events for other uids, so a fixture needs events for
  uids 1 and 2 (`tests_ha/test_agora_camera_uid_filter.py:184-194`).
- **Payload variants.** The SDK's rejoin replay uses `{uid, uint_id, video: true, ssrcId}`, with no
  `rtxSsrcId` or `cname`.

`on_add_audio_stream` (`agora_test.html:1725-1730`):
```json
{"_type": "on_add_audio_stream", "_message": {"uid": 1, "uint_id": 1, "audio": true, "ssrcId": 55555555}}
```

`on_user_offline` (`agora_websocket.py:825-859`):
```json
{"_type": "on_user_offline", "_message": {"uid": 1, "reason": "quit"}}
```

The `reason` values are unverified. When the peer that left is not our own uid, the handler sends
`renew_token`, waits a 2 s debounce, and then recovers the stream.

`on_rtp_capability_change` (`agora_websocket.py:722-735`; `agora_test.html:1734`):
```json
{"_type": "on_rtp_capability_change", "_message": {"video_codec": ["vp8", "h264"], "extmap_allow_mixed": true, "web_av1_svc": false}}
```

`on_p2p_ok` (`agora_websocket.py:664-679`). `uid` should equal our join uid; a mismatch is logged.
```json
{"_type": "on_p2p_ok", "_message": {"uid": 12345678, "proxy": false}}
```

### 3.3 `subscribe` / `unsubscribe` / `set_client_role` (CODE)

`subscribe` (`agora_websocket.py:1049-1093`):
```json
{"_id": "b2c3d4", "_type": "subscribe", "_message": {
  "stream_id": 1, "stream_type": "video", "mode": "live", "codec": "vp8",
  "p2p_id": 1, "twcc": true, "rtx": false, "extend": "", "ssrcId": 44444444
}}
```

PetKit sends `codec: "h264"` and `rtx` as given (`petkit/agora_websocket.py:531-545`).

`unsubscribe` (`agora_websocket.py:928-956`):
```json
{"_id": "c3d4e5", "_type": "unsubscribe", "_message": {"p2p_id": 1, "ortc": [], "stream_id": 1}}
```

`set_client_role` (`agora_websocket.py:1021-1047`):
```json
{"_id": "e5f6a7", "_type": "set_client_role", "_message": {"role": "host", "level": 0, "client_ts": 1790716796000}}
```

- **Mammotion must never send this.** `set_client_role(host, level=0)` makes the mower leave the channel about
  500 ms after video starts. Joining with `role: "host"` in `join_v3` is enough (project memory
  `project_agora_webrtc_fixes.md` §3; `agora_test.html:1605` has it commented out).
- **PetKit does send it,** straight after join success (`petkit/agora_websocket.py:354`).
- **Implication for `pyagora`:** role changes need a per-vendor switch.

---

## 4. Keepalive, token renewal, leave

`ping`: the SDK sends a request every 3 s (SDK:31053-31057, 31200-31224). HA-Luba does the same
(`agora_websocket.py:464-484`).
```json
{"_id": "f6a7b8", "_type": "ping"}
```

Reply:
```json
{"_id": "f6a7b8", "_result": "success", "_message": {}}
```

- **Ping reply.** The reply shape is an `_id`-correlated success, as the dispatch in §2.1 implies. The exact
  `_message` contents are unverified. HA-Luba's loop treats any `success` without `ortc` as an ack
  (`agora_websocket.py:422-428`).
- **`ping_back`.** When `REPORT_STATS` is set, the SDK follows with a fire-and-forget
  `{"_type": "ping_back", "_message": {"pingpongElapse": 42}}`.
- **Timeout.** After `PING_PONG_TIME_OUT` missed pongs, and once `now - lastMsgTime > WEBSOCKET_TIMEOUT_MIN`,
  the SDK reconnects.

`renew_token` (`agora_websocket.py:549-554`; `tests_ha/test_agora_renew_token_debounce.py:65`):
```json
{"_id": "a7b8c9", "_type": "renew_token", "_message": {"token": "TOKEN_REDACTED"}}
```

It is triggered by:
```json
{"_type": "on_token_privilege_will_expire", "_message": {}}
{"_type": "on_token_privilege_did_expire", "_message": {}}
```

- **Repeats.** `will_expire` repeats about once a second during the pre-expiry window, so renewals are debounced
  to one per 30 s (`agora_websocket.py:44-47`). `did_expire` clears the debounce.
- **Payloads unverified.** The `_message` payloads of both events are unverified; our code reads none of their
  fields.

`leave` (`agora_websocket.py:2016-2022`). It is sent only after a successful join, and is followed by closing
the socket.
```json
{"_id": "b8c9d0", "_type": "leave"}
```

**Mammotion FPV keep-alive.** This is not Agora; it goes over MQTT or BLE. On a 4G link the mower's encoder
stops publishing unless it gets `SocMul{req_encode: MulSetEncode{encode: true}}` every 3 s.
- Sources: APK `map/video/FPV4GVideoStateMannager.java:135` (`refreshInterval = 3000L`), gated by `is4GFPVLink`
  (`:234-235`); `Luba-API/pymammotion/mammotion/commands/messages/video.py:48-50`; proto
  `luba_mul.proto:113-129` (`req_encode = 11`).
- The stream is ended when the `availableTime` budget in seconds runs out (`agora_websocket.py:486-526`).
- The channel itself is opened and closed with `SocMul{set_video: MulSetVideo{position, vi_switch}}`. Position
  is `ALL` on Yuka and `LEFT` otherwise (`video.py:38-46`).

---

## 5. Error and notification frames

`on_notification` (CAPTURED shape, from a hardware-found bug; `tests_ha/test_agora_session_quit.py:10-13`,
commits `80061fe` and `27606d4`):
```json
{"_type": "on_notification", "_message": {"action": "quit", "code": 2003, "detail": "ERR_REPEAT_JOIN"}}
```

Negative case from the same test:
```json
{"_type": "on_notification", "_message": {"action": "warn", "code": 1}}
```

- **Mammotion uid sharing.** Every Mammotion stream token shares one viewer uid. A second camera joining the same
  uid while the first is established gets the first one quit with code 2003.
- **How the SDK handles it** (`handleNotification`, SDK:31150-31198): it maps `code` through the table below.
  - Code 28 with `detail` is a recover notification.
  - Code 30 is `K_VOS_FALLBACK`, whose `detail` is `"FALLBACKCN"` or `"fallback_hls"`.
  - An action of `quit` closes the connection. An action of `recover` or `retry` reconnects.

`error` (`agora_websocket.py:699-703`):
```json
{"_type": "error", "_message": {"error": "some error string"}}
```

A failed request, in the shape the SDK expects (SDK:30888-30897):
```json
{"_id": "a1b2c3", "_result": "failed", "_message": {"error_code": 2003, "error_str": "ERR_REPEAT_JOIN_CHANNEL"}}
```

The SDK reads `error_code || code`. Multi-IP errors also carry `_message.option`.

`on_p2p_lost` (SDK event; Luba-API worktree copy
`.claude/worktrees/agent-a21da380/pymammotion/agora/agora_websockets.py:462-472`):
```json
{"_type": "on_p2p_lost", "_message": {"error_code": 1, "error_str": "stun timeout"}}
```

HA-Luba's handler reads `error_code` and `error_str` from the **top level** of the frame, not from `_message`
(`agora_websocket.py:681-697`). It is probably wrong: by the §2.1 envelope, event fields live in `_message`.
Include both placements in a fixture until a capture settles it. The handler is currently unregistered
(`:198`).

`on_user_banned` codes (SDK:30746-30757): 14 is UID_BANNED, 15 is IP_BANNED, 16 is CHANNEL_BANNED.
```json
{"_type": "on_user_banned", "_message": {"error_code": 14}}
```

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
  The SDK error `ILLEGAL_AES_PASSWORD` (2028) is the gateway's answer to a bad one. `pyagora` sends none of these
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

## 8. RTM: PetKit peer messages over REST (CODE, `petkit/agora_rtm.py`)

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
3. **Gateway fingerprints.** `detail["19"]` is `;`-separated and matched to `edges_services` by index. Both hosts
   merged them into the join response's `dtlsParameters.fingerprints`, deduplicated case-insensitively; `pyagora`
   uses the connected edge's only when the gateway sends none (D26).
4. **Envelope correlation.** A frame with `_id` is a response and has `_result`; a frame without `_id` is an
   event. `_id` is 6 characters. On failure the code is in `_message.error_code`, falling back to
   `_message.code`.
5. **DTLS role.** Offer ORTC `role` is `"server"` (HA-Luba), `"client"` (PetKit) or absent (SDK). The server's
   `"client"`, `"server"` and `"auto"` map to answer `a=setup:active`, `passive` and `active` (the SDK answers
   `actpass` for `auto`, which RFC 5763 forbids in an answer; D5). Live Agora
   reports `"client"`, so the answer is `active`. The server side is `a=ice-lite`.
6. **MID extension.** `urn:ietf:params:rtp-hdrext:sdes:mid` is stripped from the answer only. Agora's edge
   hard-codes video at mid 2 internally, and HA's offer puts video at mid 1; with MID negotiated, Chrome drops
   every video RTP packet. With it stripped, BUNDLE demux falls back to payload type. The ORTC sent to Agora
   still lists it (entry 4).
7. **Extension ids** in the answer come from the offer, looked up by URI. The server's `entry` numbers are ignored.
8. **Payload types.** The server's PTs are copied into the answer unchanged, and the video PT is the only demux
   key. `on_add_video_stream.pt == 0` means no codec match (a browser without H265 decode). The join response
   lists no RTX PTs, so `subscribe.rtx` must be false unless an `rtx` codec is present. `codec` must be identical
   in `join_v3` and `subscribe`: `vp8` for Mammotion, `h264` for PetKit.
9. **SSRCs.** They come from `on_add_video_stream.ssrcId`, and `rtxSsrcId` when present, and go back in
   `subscribe.ssrcId`. HA-Luba's answer writes no `a=ssrc` lines. `on_add_video_stream` and `on_user_online` can
   arrive in either order.
10. **msid.** Project memory records the fix that made the working stream's msid stream id `"1"`, the mower's
    uid, with per-session UUID track ids. HA-Luba still initialises `_msid_stream_id = 1` and the UUIDs
    (`agora_websocket.py:174-177, 235-238`), but the current `_generate_answer_sdp` emits **no `a=msid` line**.
    A fixture asserting msid would fail against current code. Confirm the intended behaviour before pinning it.
11. **Mower uids.** They are 1, 2 and 3, one per camera slot. Each camera needs its own freshly minted stream
    token. Two joins with the same token, or a later join on the same uid, make the gateway quit the first
    session with `on_notification {action: "quit", code: 2003}`.
12. **Timers.**
    - WebSocket ping every 3 s.
    - Mammotion 4G `MulSetEncode` every 3 s.
    - PetKit RTM heartbeat every 0.5 s.
    - Renew-token debounce 30 s.
    - Peer-rejoin debounce 2 s, recovery cooldown 15 s, at most 5 attempts, count reset after 600 s.
    - Join-response timeout 15 s; per-edge connect timeout 10 s.
13. **Ordering on join.** Open the WebSocket, send `join_v3`, and wait for `_result: success` with `ortc`. Then
    generate the answer and start the ping loop. Stream events follow, then `subscribe`. For Mammotion, send no
    `set_client_role`.

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
entry keeps its slot. With `TurnCredentialStrategy.DETAIL_FIRST`, a TURN block
without edges falls back to the gateway edges with uid-derived credentials:
detail 8/4 are read only from the flag-4194310 block, because the gateway
block's detail 8 is the `vid`, not a username (the PetKit copy sent it). Detail `6` is
sent last, after 11/17/22 (the SDK sends it first; order has not been shown
to matter).

### §2.5 addendum

Fixtures redact the gateway candidate address to `198.51.100.13` (RFC 5737).

### §3.3 addendum: subscribe retry

With `subscribe_retry_attempts > 0` the session waits `subscribe_retry_delay_s`
for the subscribe ack and re-sends when none arrived, up to that many
times; with the default 0 it sends once and ignores the ack. A subscribe
goes out once both the stream announcement and the publisher's
`on_user_online` have been seen; publishers listed in the join payload
count as online.
