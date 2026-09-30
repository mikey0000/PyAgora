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

- HA-Luba: replace `custom_components/mammotion/agora_*.py` with `pyagora`
  per `docs/migration.md`; keep its six Agora tests as library tests (done
  here) and delete the local copies.
- PetKit: migration per `docs/migration.md`; the `set_client_role` question
  (Q3) must be answered on real hardware before the default changes for
  them.

## Documentation

- `docs/protocol.md` gains a captured go2rtc/pion offer and a Chrome
  trickle offer when someone records them (the fixtures today are
  reconstructed from code and comments).
