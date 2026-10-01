# Backlog

Open work only; finished items are deleted.

## Library

- **Capture PetKit and RTM.** Mammotion's gateway and AP frames are captured
  (`tests/fixtures/sessions/`, 2026-10-01). Still reconstructed: every PetKit
  exchange, RTM (§8), join and subscribe failures, `on_user_offline`, token
  expiry and `on_p2p_lost`. Enable the `pyagorartc.capture` logger at DEBUG
  (D30) in the PetKit host, turn the lines into fixtures the same way, and
  reconcile the fake (testing.md §6).

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
- `get_ice_servers` builds `turn:` URLs on the fixed ports 3478/443 as both
  hosts shipped; the SDK uses the port the AP returns for each TURN edge
  (`t2`, D32). Follow it once a relay-only session on the AP's port is
  captured.
- `TurnCredentialStrategy.DETAIL_FIRST` goes at the next major version (D32).

## Hosts

- HA-Luba: merge the `pyagorartc-migration` branch (`docs/migration.md` §2)
  once the hardware run below passes.
- HA-Luba: run one WiFi and one 4G session on a Luba 2 and a Yuka across all
  camera uids to validate the migration (Q2, Q6, Q10, Q11; migration §4).
- HA-Luba: give `CloseReason.PING_TIMEOUT` (D31) its own viewer message in
  `camera.py::_CLOSE_MESSAGES`; until then it falls through to "Stream lost".
- HA-Luba: pass `gateway_edge_offset=target_uid - 1` permanently (D33,
  migration §2.4.1) and delete the temporary `_Q20_EXPERIMENT` switch from
  `camera.py`.
- Edge-aware placement: each camera's offset indexes its own AP answer, and
  the answers' edge lists can differ (Q20 run 2), so two cameras can still
  land on one edge. A host-supplied set of edges in use, skipped by
  `_connect`, would close that; wait for a recorded collision first.
- pymammotion drops its unused `sdp-transform`, `websockets`, `webrtc-models`
  requirements.
- PetKit: migration per `docs/migration.md` §3. One go2rtc session per
  camera model at DEBUG answers Q18 first (it decides the default of
  `subscribe_requires_online`, D28), then Q3, Q7 and
  Q13 (migration §4). `send_set_client_role` and `end_on_p2p_lost` stay on
  for PetKit until those runs say otherwise.

## Documentation

- `docs/protocol.md` gains a captured go2rtc/pion offer and a Chrome
  trickle offer when someone records them (the fixtures today are
  reconstructed from code and comments).
