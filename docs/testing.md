# Testing

This is the testing constitution. `tests/meta/test_conventions.py` asserts the
mechanical parts; review catches the rest.

## 1. Tiers

| Tier | Directory | Touches | Runs |
|---|---|---|---|
| unit | `tests/unit/` | one module, hand-written fakes for the layer below, no sockets, no real clock | every commit, < 5 s |
| integration | `tests/integration/` | the real `AgoraSession`, `AgoraAPClient` and `RtmRestClient` against `tests/fakegateway` over loopback (`test_session_*.py`, `test_ap_*.py`), and the fake's own contract tests (`test_fakegateway_contract_*.py`) | every commit, < 30 s |
| regression | `tests/regression/` | cross-module pins for bugs that escaped; single-module pins live beside the module | every commit |
| live | `tests/live/` | real Agora infrastructure with real credentials from the environment; marked `live`, skipped when unset | on demand |

A unit test that imports `tests.fakegateway` is an integration test in the
wrong directory. An integration test that patches a private method is a unit
test in the wrong directory.

## 2. Layout mirrors the package

```
pyagorartc/sdp/answer.py            →  tests/unit/sdp/test_answer.py
pyagorartc/ap/response.py           →  tests/unit/ap/test_response.py
pyagorartc/session/session.py       →  tests/unit/session/test_session.py
```

One test module per source module. Split by concern
(`test_token_manager_refresh.py`) when a module passes ~500 lines, never by
number. Tests are grouped in classes named for the behaviour under test
(`class TestGetAccessToken:`), with test names that read as sentences:
`test_returns_cached_token_when_fresh`, `test_raises_terminal_after_client_credentials_rejected`.

Shared builders live in one place:

- `tests/_helpers.py` — envelope and JSON builders used by more than one tier.
- `tests/unit/_fakes.py` — `FakeGatewayTransport`, `FakeGatewayConnection`,
  `ManualClock`, `ManualSleep` (an `asyncio.sleep` on it), `Recorder` (a host
  callback that records its calls; the integration tier uses it too).
  Hand-written, same interface as the real thing, scripted frames, recorded
  sends.
- `tests/unit/<pkg>/_helpers.py` — builders used by more than one module in
  that package.
- `tests/unit/conftest.py` and `tests/integration/conftest.py` — tier fixtures
  (the no-network guard; the fake gateway, its clock and raw clients).
- `tests/conftest.py` — global autouse safety nets only: the `credentials`
  fixture and the guard that fails a test whose setup or call-phase logs
  carry any value in `SECRET_VALUES` (`leaked_secrets` in `tests/_helpers.py`,
  itself pinned by `tests/meta/test_guards.py`). `tests/unit/conftest.py` adds the no-network guard (the
  integration tier needs loopback).

Four copies of `make_join_ok` is the failure this rule exists to stop.

Modules that need no mirror are listed in `EXEMPT_FROM_MIRRORING` in
`tests/meta/test_conventions.py`, each with a one-line reason: `const.py`,
`exceptions.py` and `session/transport.py` (protocols plus a thin adapter the
integration tier exercises; the exemption goes if the adapter grows logic).
Everything else is mirrored.

## 3. Doubles

Preference order: real object → hand-written fake. `unittest.mock` is not
imported anywhere under `tests/`: a `MagicMock` answers every attribute
truthily forever, so a renamed method keeps passing, and `create_autospec`
still lets a test assert on plumbing instead of outcomes. `monkeypatch` is for
environment variables and `const` values, not for replacing collaborators.

Never mock the unit under test. Assert on outcomes (the returned model, the
raised exception, the recorded request) rather than on call plumbing, unless
the call *is* the contract ("sends exactly one refresh for a burst of 401s").

## 4. Time and concurrency

- Anything that reads a clock takes it as a parameter (`clock: Callable[[],
  float]`) and tests pass a controllable one. `time-machine` with
  `tick=False` is the fallback for code that cannot be parameterised.
- `await asyncio.sleep(0.1)` is not synchronisation. Wait on an
  `asyncio.Event`, a future, or `asyncio.sleep(0)` for exactly one loop turn.
- Bound every wait with `asyncio.wait_for`.
- `asyncio_mode = "auto"`; no `@pytest.mark.asyncio` decorators.
- Concurrency tests (the 401 burst, the refresh lock) use a fake whose
  responses are gated on an `asyncio.Event` so the interleaving is
  deterministic.

## 5. Secrets in tests

Fixture credentials are obviously fake (`client_id="cid-test"`,
`client_secret="not-a-real-secret"`). Tests assert that `repr()` and
`str(exc)` do **not** contain them. Live tests read credentials from
`PYAGORA_LIVE_APP_ID` / `PYAGORA_LIVE_CHANNEL` / `PYAGORA_LIVE_TOKEN` /
`PYAGORA_LIVE_UID` and never print them.

## 6. The fake gateway and fixtures

`tests/fixtures/` holds redacted recordings and reconstructions of every
wire shape (`docs/protocol.md` says which is which): SDP offers, AP
responses, gateway frames, RTM responses. Unit tests load them through
`tests/_helpers.py::load_fixture`; nothing hand-types a protocol frame in
a test body.

`tests/fakegateway/` is an in-process stand-in for Agora: a `websockets`
server that speaks the recorded gateway protocol (join, subscribe, stream
events, ping, renew, quit, p2p_lost) plus an aiohttp app serving the AP
`choose_server`/`update_ticket` endpoints and the RTM `peer_messages`
endpoint, with a `/control` route for fault injection (reject join, delay,
drop socket, stop answering pings, announce a peer, make the peer leave, expire the token). It is
the executable form of `protocol.md`; when they disagree, fix the fake and
record why in `open_questions.md`.

Integration tests point the library at it through the clients' `hosts=` /
`transport=` arguments; nothing reads real time (the fake takes a
`ManualClock`). A session under test (`tests/integration/_helpers.py::session_rig`,
the `new_session` fixture) shares the fake's clock and `scheduler.sleep`, and
dials through `LoopbackEdgeTransport`: the real `WebsocketsTransport`, with the
`wss://a-b-c-d.<edge domain>` URL the session builds dialled as `ws://a.b.c.d`. The fake speaks plain `http`/`ws`, so TLS verification
(D10) is exercised only at the `_post` / `connect` seams in the unit tier.

## 7. Regression contract

A regression test:

1. was written against the broken code and seen red;
2. is marked `@pytest.mark.regression`;
3. is named for the behaviour, not the ticket;
4. has a docstring stating what the code did wrong;
5. lives beside the module it pins, or in `tests/regression/` when it spans
   modules.

## 8. Coverage and gates

- `pytest --cov=pyagorartc` must not drop below the number in
  `pyproject.toml`. Coverage is a floor, not a target; a line covered by a
  test that asserts nothing is not tested.
- `tests/meta/test_conventions.py` checks: mirroring, no `unittest.mock`, no
  `asyncio.sleep(<non-zero>)` outside `tests/fakegateway/` (whose delay knob is
  behaviour under test), no `tests.fakegateway` import under `unit/`,
  regression marker ↔ docstring, no real hostnames or secret-shaped literals
  in tests, no real host address or secret in any file under
  `tests/fixtures/` (IPs must be loopback or RFC 5737/3849), fakes only in
  `tests/unit/_fakes.py` and `tests/fakegateway/`, builders defined once, the
  layer map, top-level imports only, `__all__` importable and sorted, no
  self-driven event loops.
- Pre-commit runs ruff, ruff-format, ty and the unit tier.

## 9. Writing a test

```python
class TestJoin:
    async def test_subscribes_to_a_stream_announced_after_join(self, credentials: ChannelCredentials) -> None:
        conn = FakeGatewayConnection()
        conn.feed(load_json_fixture("gateway/join_ok.json"))
        clock = ManualClock()
        session = AgoraSession(
            credentials, ap_response(), transport=FakeGatewayTransport(conn), clock=clock, sleep=ManualSleep(clock)
        )

        answer = await asyncio.wait_for(session.join(load_fixture("sdp/chrome_recvonly_offer.sdp"), "s1"), timeout=1)
        conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await asyncio.wait_for(conn.wait_for_sent(3), timeout=1)

        assert "a=setup:active" in answer  # join_ok.json reports gateway role "client" (D5)
        assert conn.sent_of_type("subscribe")[0]["_message"]["stream_id"] == 1
```

Arrange, act, assert, separated by blank lines. One behaviour per test. The
assertion says what the contract is. A fake's `wait_for_sent(n)` is how a
test waits for the session to act; `asyncio.sleep(0)` is only for "exactly
one loop turn".
