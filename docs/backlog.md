# Backlog

Open work only; finished items are deleted.

## Library

- **Record real gateway frames.** No server-to-client WebSocket frame has ever
  been captured locally; `docs/protocol.md` §2–§5 are reconstructions. One
  Frida run (`Luba-API/scripts/frida/agora-ws.js`) or one HA session with the
  session logger at DEBUG turns them into fixtures under `tests/fixtures/`.

- `tests/integration/test_ap_client.py` and `test_rtm_client.py` against the
  fake gateway's HTTP routes, covering the real `_post` seams (multipart
  field, TLS flag, timeout, non-JSON bodies).
- Move the shared `RecordedPoster` from the ap and rtm test modules into
  `tests/unit/_fakes.py`; builders into per-package `_helpers.py`.
- Fake gateway knobs to fail the gateway AP block and to answer a malformed
  body, so `APRejectedError` and the non-JSON `_post` path get integration
  coverage.
- A fake gateway knob that omits `dtlsParameters.fingerprints` from the join
  response, so D26's fill-in gets integration coverage (Q19).
- `SessionOptions.subscribe_requires_online: bool = True` (Q18): `False`
  subscribes on `on_add_video_stream` alone, as PetKit's copy did
  (`agora_websocket.py:440-470`). Add it before PetKit ships if its gateway
  sends no `on_user_online`; with it, a DEBUG line when a stream is held for
  presence, since today `_maybe_subscribe` returns silently.
- A way for a host to filter the join's inline candidates. PetKit dropped
  host candidates before the join (`whep_proxy.py:170-173`); `join()` now
  sends every `a=candidate:` in the offer. The workaround (strip the lines,
  `add_ice_candidate` the filtered ones, migration §3.6) works; decide after
  the PetKit run whether an option is worth it.
- Send AP detail `6` without putting `string_uid` into the join. PetKit's AP
  request always carried `6 = str(uid)` (`agora_api.py:320-321`) and its join
  none (`agora_websocket.py:686`); `ChannelCredentials.string_uid` couples
  the two (Q9).
- Post-join trickle candidates once Q4 is answered.
- ICE restart / rejoin with `rejoin_token` (the field is stored today and
  never used).
- `update_ticket` is implemented and untested against a live edge; the JS
  SDK uses it on ticket refresh.
- A `live` tier smoke test that joins a real channel from environment
  credentials and asserts an answer SDP arrives.
- A retry policy for `AgoraAPClient` beyond the primary/backup host loop,
  once real failure modes are recorded.

## Hosts

- HA-Luba: merge the `pyagorartc-migration` branch (`docs/migration.md` §2)
  once the hardware run below passes.
- HA-Luba: run one WiFi and one 4G session on a Luba 2 and a Yuka across all
  camera uids to validate the migration (Q2, Q5, Q6, Q10, Q11; migration §4).
- pymammotion drops its unused `sdp-transform`, `websockets`, `webrtc-models`
  requirements.
- PetKit: migration per `docs/migration.md` §3. One go2rtc session per
  camera model at DEBUG answers Q18 first (it decides whether
  `subscribe_requires_online` is needed before release), then Q3, Q7 and
  Q13 (migration §4). `send_set_client_role` and `end_on_p2p_lost` stay on
  for PetKit until those runs say otherwise.

## Documentation

- `docs/protocol.md` gains a captured go2rtc/pion offer and a Chrome
  trickle offer when someone records them (the fixtures today are
  reconstructed from code and comments).
