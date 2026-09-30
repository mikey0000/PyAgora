# Code style

Ruff and ty enforce most of this (`pyproject.toml`); the rest is convention
and gets caught in review. Where this document and a linter disagree, fix the
linter configuration, not the code.

## Language and tooling

- Python 3.13 or newer. Use the modern syntax that implies: `type X = ...`
  aliases, PEP 695 generics, `match`, `Self`, `override`.
- `uv` manages the environment. `uv run ruff check --fix .`,
  `uv run ruff format .`, `uv run ty check pyagora/`, `uv run pytest`.
- Line length is 120. Ruff `select = ["ALL"]` with a short, justified ignore
  list; every ignore has a comment saying why.

## Structure

- **One concern per module.** A module's docstring says what it owns in one
  sentence. If you cannot write that sentence, split the module.
- **Top-level imports only.** No imports inside functions. Circular-import
  pressure is solved with a `TYPE_CHECKING` block for type-only names, or by
  moving the code to the right layer.
- **Layers import downward only** (see `architecture.md` §1). `sdp/` never
  imports `session/`; `ap/` and `rtm/` never import each other or `session/`.
- **Protocols over base classes** for anything a test replaces:
  `GatewayTransport` and `GatewayConnection` are `typing.Protocol`s. Inheritance is for
  shared implementation (`ApiGroup`), not for polymorphism.
- **Public names are curated.** Each package `__init__.py` re-exports what the
  layer above needs and nothing else. `pyagora/__init__.py::__all__` is
  the supported surface.

## Typing

- Everything is annotated, including tests. `ty` runs clean on the package.
- No `Any` in a public signature. Raw JSON is `Mapping[str, object]` or
  `JsonValue`; `Any` is confined to the decode boundary in `models/`.
- Prefer `X | None` to `Optional[X]`, `list[X]` to `List[X]`, `Mapping` for
  read-only parameters, concrete types for return values.
- Enums for closed sets the specification enumerates. Wire enums subclass
  `TolerantIntEnum` or `TolerantStrEnum` (`models/common.py`) so an unknown
  value decodes to `UNKNOWN` instead of raising.

## Async

- Every I/O method is `async def`. Nothing blocks the loop.
- `asyncio.Lock` guards shared mutable state that spans an `await`; re-check
  the condition after acquiring the lock.
- Bound every external wait with a timeout taken from `const.py`; never a
  literal at the call site.
- No `asyncio.create_task` fire-and-forget inside the library. Tasks are owned
  and cancelled in `close()`.

## Naming

- Modules and functions are `snake_case`; classes are `CapWords`; constants are
  `UPPER_SNAKE`. Wire names (`deviceId`) appear only as `Alias(...)` strings
  in models; Python attributes are always `snake_case` (`device_id`).
- Endpoint methods are verbs named for what the caller wants, not the HTTP
  verb: `devices.list()`, `devices.get(device_id)`, `actions.start(...)`,
  `work_reports.search(...)`. The path lives in the method body.
- Exceptions end in `Error`. Protocols are named for the role
  (`Requester`, `TokenProvider`), not `IFoo` or `FooProtocol`.
- Booleans read as predicates: `is_fresh`, `has_more`, `ok`.
- Private is one underscore. Do not reach into another module's `_private`
  names; surface a public property.

## Models and wire frames

- Value types are `@dataclass(frozen=True)`; anything holding a secret has a
  custom `__repr__` that redacts it (`models.py` shows the pattern).
- Gateway frames are built and parsed in `session/messages.py` only, as
  plain dicts with the exact wire keys; a typed dataclass wraps what the
  session reads. Nothing else spells a wire key.
- Unknown frame types and unknown keys are ignored after one DEBUG line
  (Constitution §7); malformed text makes `parse_frame` return `None`. A required key missing from a frame the session depends
  on raises `SdpError`/`JoinRejectedError` with the key named, never the
  frame body.
- Timestamps on the wire are milliseconds (`client_ts`); internally the
  session uses the injected monotonic `clock`.

## Errors

- Raise the exceptions in `exceptions.py`; never `raise Exception` or a bare
  `ValueError` for an API condition.
- Exception messages describe the condition, not the fix, and never include
  a secret or a full response body. Attach structured fields (`code`, `msg`,
  `request_id`, `status`) as attributes.
- Do not `try/except` an exception a preceding guard already rules out.
- Do not swallow. Log-and-continue is allowed only in a `close()` path and
  in the one case D16 names (a host's `on_token_updated` callback).

## Logging

- One module logger: `_LOGGER = logging.getLogger(__name__)`.
- DEBUG for request/response outlines (method, path, status, `request_id`),
  INFO for token rotation (fingerprint only), WARNING for a rejected refresh
  and for an unknown enum value (once per value, D10), ERROR never (the
  exception carries it).
- Use `%s` formatting, not f-strings, in log calls.

## Comments and docstrings

- Default to no comment. Write one when a competent reader could not infer
  the constraint from the code: a server quirk, a spec ambiguity, a workaround.
- One or two lines. Longer explanations belong in `docs/`, linked by decision
  number (`# D7: client credentials may be re-granted once`).
- Never a comment that restates the next line, never a comment that explains
  what the code used to be, never a section divider.
- Every public module, class and method has a docstring: one summary line,
  then only what the signature does not say (units, spec gaps, raised
  exceptions). Google style sections (`Args:`, `Raises:`) when there is more
  than one thing to say.

## Small things

- Walrus (`:=`) where it removes a line without hiding the binding.
- `pathlib`, `orjson`, f-strings, `Self`.
- No magic numbers at a call site: name it in `const.py` or the module.
- No dead code, no commented-out code, no `TODO` without a backlog entry.
