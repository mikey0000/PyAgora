# CLAUDE.md

Guidance for agents working in this repository. Read `CONSTITUTION.md` first;
it is short and it is binding.

## What this is

`pyagorartc`: an async Python client for Agora RTC signalling, reverse-engineered
from Agora's Web SDK and hardened by two Home Assistant integrations
(Mammotion mowers, PetKit cameras). It is host-agnostic: no Home Assistant, no
vendor library, no vendor behaviour except through documented extension
points.

## Commands

```bash
uv sync
uv run ruff check --fix . && uv run ruff format .
uv run ty check pyagorartc/
uv run pytest                      # all tiers except live
uv run pytest tests/unit           # fast tier
uv run pre-commit run --all-files
```

## Where things are

| Need | Read |
|---|---|
| the rules | `CONSTITUTION.md` |
| the map (layers, session lifecycle, single homes, recipes) | `docs/architecture.md` |
| the protocol as we know it | `docs/protocol.md`, `tests/fixtures/` |
| how to write code / tests | `docs/code_style.md`, `docs/testing.md` |
| why something is the way it is | `docs/decisions.md` (cite as D7) |
| what we do not know | `docs/open_questions.md` (cite as Q2) |
| how a host adopts the library | `docs/migration.md` |
| what is left to do | `docs/backlog.md` |

## Rules of work

- **Audit before adding.** Every concern has a single home
  (`architecture.md` §3). Extend the existing site.
- **Traceability.** A change to what goes on the wire cites its source
  (fixture, SDK behaviour, or a regression test that was red).
- **No host or vendor imports** anywhere under `pyagorartc/`. The meta tests
  enforce it.
- **Tests before merge, in the right tier.** Regression tests are seen red
  first and marked `regression`. Launch `test-reviewer` over any test file you
  touched and fix its blocking findings before reporting done.
- **Reviews.** Non-trivial changes get `code-reviewer` before they are
  reported complete. The author fixes; the reviewer does not rewrite.
- **Decisions are written down.** New choice → `Dn`; new unknown → `Qn`.
- **No secrets anywhere** in logs, reprs, exceptions, fixtures (redact), tests
  or commit messages.
- **Comments say why, in one or two lines, or do not exist.**
- **Commits**: imperative subject, one change per commit, no attribution
  trailers.
