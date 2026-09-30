# Constitution

The rules that do not bend. `docs/` explains how to apply them; this file says
what they are. Breaking one needs a new numbered entry in `docs/decisions.md`
that supersedes the rule, not a quiet exception.

## 1. Protocol fidelity is traceable

`pyagora` speaks a signalling protocol that Agora has not published for
Python. Every behaviour on the wire traces to one of: a recorded exchange
(`tests/fixtures/`), the documented behaviour of Agora's Web SDK, or a fix
that carries a regression test. A behaviour that traces to nothing is a guess,
and a guess is written down in `docs/open_questions.md` before it ships. The
hard-won fixes (MID extension stripping, DTLS role, no `set_client_role` after
join, msid stream id, peer recovery) are pinned by tests, not by comments.

## 2. One library, many vendors, no host

Mammotion mowers and PetKit cameras both publish through Agora; other vendors
will. The library knows channels, tokens, edges, SDP and gateway messages. It
does not know mowers, feeders, `pymammotion`, `pypetkitapi`, or Home
Assistant. Vendor-specific behaviour (a keep-alive the device needs, a way to
re-request the stream) enters through callbacks and options on the session,
never through an import. If a vendor needs something the session cannot
express, the session grows a documented extension point.

## 3. Layers point one way

```
sdp  ←  ap  ←  session  ←  (host)        rtm  ←  (host)
```

`sdp/` is pure: text and dicts in, text and dicts out, no I/O (it may read
`const` values). `ap/` talks
HTTP to Agora's access points. `session/` drives one WebSocket gateway
session and composes the two. `rtm/` is independent of all three. Nothing
imports upward. The public surface is
`pyagora.__all__`.

## 4. All I/O is asynchronous and owned

Every network call is `async`. Background work (ping, keep-alive, peer
recovery, restart) runs in tasks the session owns and cancels when it ends
(`close()` or any other ending); nothing is fire-and-forget. A host may supply its own aiohttp
session; the library never closes one it did not create.

## 5. Errors are typed and scoped

A failed edge discovery, a rejected join, a lost gateway connection and a
malformed message are different exceptions with different recoveries, and
they say which without a string match. Transient failures never change
credential or session state. The session reports its lifecycle through one
state enum and one event callback, not through log lines.

## 6. Secrets stay secret

Tokens, tickets, credentials, TURN passwords and channel encryption keys never
appear in log lines, `repr`, or exception messages. Logs may carry a
fingerprint (short hash) and nothing more.

## 7. Wire messages are tolerant, fixtures are exact

An unknown message type, an extra field or a missing optional field never
crashes the session; it is logged once at DEBUG and ignored. Fixtures in
`tests/fixtures/` are byte-exact recordings (redacted) and tests assert
against them exactly; when the wire changes, the fixture changes with a
commit that says why.

## 8. Tests are part of the deliverable

Nothing merges without tests in the right tier (`docs/testing.md`). Pure
code (SDP, ORTC, payload building, ICE selection) is tested directly.
The session is tested against an in-process fake gateway that speaks the
recorded protocol. Doubles are real objects or hand-written fakes; a bare
`MagicMock` is not a test double.

## 9. Documentation lives outside the code

Design is in `docs/`. A comment states a constraint the code cannot express,
in one or two lines. Every non-obvious choice is a numbered decision; every
unknown is a numbered open question; every intended change is a backlog
entry.

## 10. The public surface is deliberate

`pyagora.__all__` lists the supported API. Anything else may change without
notice. Semver; a breaking change to a listed name bumps the major version.
Both known hosts (Mammotion, PetKit) have a written migration path in
`docs/migration.md` before a release that changes the surface.
