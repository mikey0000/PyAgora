# pyagora

Async Python client for Agora RTC signalling, for WebRTC consumers that are
not the Agora SDK: a browser behind Home Assistant, go2rtc, pion.

A device publishes video to an Agora channel. `pyagora` finds the Agora
edge, turns the consumer's SDP offer into Agora's ORTC, joins the channel
over the gateway WebSocket, subscribes to the stream, and hands back an
answer SDP. Media then flows directly between the consumer and the Agora
edge. It also sends Agora RTM peer messages over REST, which some vendors
use as a device control channel.

It knows channels, tokens, edges, SDP and gateway messages. It does not
know any vendor. Mammotion mowers and PetKit cameras are the two known
hosts; vendor behaviour plugs in through callbacks and options.

## What it does not do

- **No media.** It never touches RTP. The consumer receives the stream.
- **No decryption.** Agora channel encryption (`aes_*`) is applied inside
  the SDK's media path. The fields are modelled but never sent (the SDK
  RSA-wraps the secret), and a plain WebRTC consumer cannot decode an
  encrypted stream anyway (D20, Q17).
- **No relay.** It does not run go2rtc, a TURN server, or an HTTP/WHEP
  endpoint. Those are host jobs.
- **No vendor API.** Credentials arrive already fetched.
- **No recovery policy.** It times peer recovery; the host decides what
  recovery means.

## Status

Alpha (`0.x`). The API below is the target surface; `pyagora.__all__` is
the supported part of it (Constitution §10). Wire behaviour comes from
recordings, the Agora Web SDK 4.24.3 and two shipped integrations; what is
still a guess is listed in [`docs/open_questions.md`](docs/open_questions.md).

## Install

```
pip install pyagora
```

Python 3.13+. Dependencies: `aiohttp`, `websockets>=13.1`, `sdp-transform`.

## Usage

```python
import aiohttp
from pyagora import AgoraAPClient, AgoraSession, ChannelCredentials, CloseReason, PyAgoraError, SessionOptions


async def on_closed(reason: CloseReason) -> None:
    print("session ended:", reason)


async def on_peer_left(uid: int) -> None:
    print("publisher", uid, "left; ask the device to publish again")


async def stream(viewer, creds: ChannelCredentials) -> None:
    async with aiohttp.ClientSession() as http:
        ap = await AgoraAPClient(http).choose_server(creds)
        viewer.set_ice_servers(ap.get_ice_servers())       # before the viewer makes its offer
        offer_sdp = await viewer.create_offer()

        session = AgoraSession(creds, ap, options=SessionOptions(target_uid=1),
                               on_closed=on_closed, on_peer_left=on_peer_left)
        try:
            answer_sdp = await session.join(offer_sdp, session_id="viewer-1")
            await viewer.set_answer(answer_sdp)
            await viewer.wait_until_done()
        except PyAgoraError as err:
            print("join failed:", err)
        finally:
            await session.close()                          # cancel and await owned tasks, leave, close
```

`viewer` stands for whatever produces the offer. One session serves one
offer; a new offer is a new `AgoraSession`. Candidates known before
`join()` go in with `session.add_ice_candidate(...)`.

## Vendor policy

| Concern | Library provides | Host provides |
|---|---|---|
| Token refresh | `token_provider`, 30 s debounce (D8) | the vendor call that mints a token |
| Device keep-alive | `keepalive`, `keepalive_interval_s`, `deadline` (D15) | the call to the device |
| Stream recovery | peer-recovery timing, `on_peer_left(uid)` (D14) | re-requesting the stream |
| Which camera | `SessionOptions.target_uid` | slot → uid |
| Consumer quirks | `declare_remote_video_ssrc`, `disable_audio`, `instant_video`, subscribe retry | choosing them |
| `set_client_role` after join | `send_set_client_role`, default off (D6) | knowing the device tolerates it |
| RTM control | `RtmRestClient.send_peer_message` (D18) | the commands and their cadence |
| Viewer transport | answer SDP, ICE servers, candidate helpers | views, auth, relays |

The full table is in [`docs/architecture.md`](docs/architecture.md) §4.

## Errors

All exceptions derive from `PyAgoraError`. None carries a token, ticket,
credential or key.

| Exception | Meaning |
|---|---|
| `APError` | Edge discovery failed on every access-point host. |
| `APRejectedError` | The access point answered, but rejected every service (`.codes`). |
| `SdpError` | The offer could not be converted, or no answer could be built. |
| `GatewayConnectError` | No gateway edge accepted the WebSocket, or it closed (or stalled) before the join result. |
| `JoinRejectedError` | The gateway refused the join (`.code`). |
| `JoinTimeoutError` | No join result in time. |
| `SessionClosedError` | The session has already ended. |
| `RtmError` | An RTM peer message was not accepted (`.status`, `.code`). |

A failed join raises; it never returns a made-up answer (D9). A running
session reports its end once, through `on_closed(CloseReason)` (D14).

## Documentation

- [`CONSTITUTION.md`](CONSTITUTION.md): the rules.
- [`docs/architecture.md`](docs/architecture.md): layers, lifecycle, single homes.
- [`docs/decisions.md`](docs/decisions.md): why (D1–D27).
- [`docs/open_questions.md`](docs/open_questions.md): what is not settled (Q1–Q19).
- [`docs/protocol.md`](docs/protocol.md): what the wire looks like.
- [`docs/migration.md`](docs/migration.md): moving the Mammotion and PetKit integrations onto this library.
- [`docs/testing.md`](docs/testing.md): how it is tested.

## Licence

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
