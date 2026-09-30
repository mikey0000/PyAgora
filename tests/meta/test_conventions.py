"""Mechanical checks of the conventions in docs/testing.md, docs/code_style.md and CONSTITUTION.md.

Each class is one rule. A rule is a pure ``_<rule>(path, tree) -> list[Offence]`` function: one test drives it over
the real tree and lists every offending ``path:line``; the others feed it snippets so the check cannot pass vacuously.
"""

from __future__ import annotations

import ast
import importlib
import ipaddress
import re
import sys
from typing import TYPE_CHECKING

import pytest

from tests.meta._helpers import (
    FAKEGATEWAY_DIR,
    FIXTURES_DIR,
    META_DIR,
    PACKAGE,
    PACKAGE_DIR,
    REGRESSION_DIR,
    REPO_ROOT,
    TESTS_DIR,
    UNIT_DIR,
    Offence,
    calls,
    collect_tests,
    function_params,
    functions,
    import_aliases,
    imported_modules,
    is_under,
    is_within,
    parse,
    python_files,
    qualified_name,
    rel,
    report,
    string_literals,
    top_level_defs,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    type Rule = Callable[[Path, ast.Module], list[Offence]]

EXEMPT_FROM_MIRRORING = frozenset(
    {
        "const.py",  # constants only; exercised through every module that reads them
        "exceptions.py",  # the hierarchy is exercised by every test that expects a raise
        # Protocols plus a thin websockets adapter the integration tier covers; drop this if the adapter grows logic.
        "session/transport.py",
    }
)

MOCK_MODULES = ("unittest.mock", "mock", "pytest_mock")
MOCKER_FIXTURES = frozenset({"mocker", "class_mocker", "module_mocker", "package_mocker", "session_mocker"})

EVENT_LOOP_CALLS = frozenset(
    {
        "asyncio.run",
        "asyncio.Runner",
        "asyncio.get_event_loop",
        "asyncio.new_event_loop",
        "asyncio.set_event_loop",
    }
)

SOCKET_TEST_NAMES = frozenset({"TestServer", "TestClient", "RawTestServer"})
SOCKET_TEST_FIXTURES = frozenset({"aiohttp_client", "aiohttp_server", "aiohttp_raw_server"})
UNIT_FORBIDDEN_MODULES = (
    "tests.fakegateway",
    "aiohttp.test_utils",
    "websockets.asyncio.server",
    "websockets.server",
    "websockets.serve",
    "websockets.sync.server",
    "websockets.legacy.server",
    "aiohttp.web.AppRunner",
    "aiohttp.web.TCPSite",
    "aiohttp.web.run_app",
)

REAL_HOSTS = ("agora.io", "sd-rtn.com")
JWT_SHAPE = re.compile(r"eyJ[\w-]*\.[\w-]*\.")
SECRET_RUN = re.compile(r"[A-Za-z0-9+/=_-]{41,}")
SECRET_SEGMENT = re.compile(r"[A-Za-z0-9+/=]{20,}")
IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?!\d|\.\d)")
IPV6_CANDIDATE = re.compile(r"[0-9A-Fa-f]*:[0-9A-Fa-f:.]*:[0-9A-Fa-f.]*")
# RFC 5737 documentation ranges, loopback, and SDP's placeholder 0.0.0.0.
FIXTURE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "0.0.0.0/32",
        "::1/128",
        "::/128",
        "2001:db8::/32",
    )
)

BUILDER = re.compile(r"_?make_\w+")
FAKE_CLASS = re.compile(r"_?Fake")

FORBIDDEN_EVERYWHERE = (
    "homeassistant",
    "webrtc_models",
    "pymammotion",
    "pypetkitapi",
    "go2rtc_client",
    "paho",
    "aiofiles",
    "tests",
)

# layer (first path part, or stem of a top-level module) → the other package modules it may import
LAYER_RULES: dict[str, tuple[str, ...]] = {
    "const": (),
    "exceptions": (),
    "models": ("exceptions", "const"),
    "sdp": ("models", "exceptions", "const"),
    "ap": ("models", "exceptions", "const"),
    "rtm": ("models", "exceptions", "const"),
    "session": ("sdp", "ap", "models", "exceptions", "const"),
}
# layers that may import no third-party package beyond these (sdp/ is pure: text and dicts, no I/O)
THIRD_PARTY_ALLOWED: dict[str, tuple[str, ...]] = {"sdp": ("sdp_transform",)}


def _source_modules() -> list[Path]:
    return python_files(PACKAGE_DIR)


def _unit_test_files() -> list[Path]:
    return python_files(UNIT_DIR)


def _sweep(rule: Rule, paths: Iterable[Path]) -> list[Offence]:
    return [offence for path in paths for offence in rule(path, parse(path))]


def _snippet(rule: Rule, source: str, path: Path) -> list[Offence]:
    return rule(path, ast.parse(source))


SNIPPET_TEST = UNIT_DIR / "session" / "test_snippet.py"
SNIPPET_REGRESSION_TEST = REGRESSION_DIR / "test_snippet.py"


class TestMirroring:
    """testing.md §2: "One test module per source module", laid out to mirror the package."""

    def test_every_source_module_has_a_unit_test_module(self) -> None:
        """§2: ``pyagora/<dir>/<mod>.py`` → ``tests/unit/<dir>/test_<mod>.py`` (``models.py`` → ``test_models.py``)."""
        missing = []
        for path in _source_modules():
            relative = path.relative_to(PACKAGE_DIR)
            if path.stem.startswith("_") or relative.as_posix() in EXEMPT_FROM_MIRRORING:
                continue
            expected = UNIT_DIR / relative.parent / f"test_{path.stem}.py"
            if not expected.is_file():
                missing.append(f"{rel(path)} → expected {rel(expected)}")

        assert not missing, report("Source modules without a mirrored unit-test module", missing)

    def test_every_unit_test_module_names_an_existing_source_module(self) -> None:
        """§2: a ``tests/unit/**/test_<mod>[_<concern>].py`` must pin a real ``pyagora`` module."""
        orphans = []
        for path in _unit_test_files():
            if not path.stem.startswith("test_"):
                continue
            source_dir = PACKAGE_DIR / path.parent.relative_to(UNIT_DIR)
            stems = {p.stem for p in source_dir.glob("*.py")} if source_dir.is_dir() else set()
            if not _names_a_module(path.stem.removeprefix("test_"), stems):
                orphans.append(f"{rel(path)} → no module in {rel(source_dir)}/ matches")

        assert not orphans, report("Unit-test modules that mirror no source module", orphans)

    def test_every_exemption_names_an_existing_module(self) -> None:
        """§2: a stale exemption would silently exempt whatever file next takes its name."""
        stale = [entry for entry in EXEMPT_FROM_MIRRORING if not (PACKAGE_DIR / entry).is_file()]

        assert not stale, report("EXEMPT_FROM_MIRRORING entries with no source module", stale)

    @pytest.mark.parametrize(
        ("name", "stems", "matches"),
        [
            ("session", {"session"}, True),
            ("session_recovery", {"session"}, True),
            ("base", {"_base"}, True),
            ("gone", {"session"}, False),
        ],
    )
    def test_matches_test_modules_to_source_stems(self, name: str, stems: set[str], *, matches: bool) -> None:
        assert _names_a_module(name, stems) is matches


def _names_a_module(name: str, stems: set[str]) -> bool:
    """``name`` is ``<mod>`` or ``<mod>_<concern>`` for a module stem, where ``_base`` answers to ``base``."""
    candidates = [name] + [name[:i] for i, char in enumerate(name) if char == "_"]
    return any(c in stems or f"_{c}" in stems for c in candidates if c)


def _mocking(path: Path, tree: ast.Module) -> list[Offence]:
    offences = [
        Offence(path, line, f"imports {module}")
        for line, module in imported_modules(tree, path)
        if any(is_within(module, m) for m in MOCK_MODULES)
    ]
    offences.extend(
        Offence(path, fn.lineno, f"{fn.name}() takes the {param!r} fixture")
        for fn in functions(tree)
        for param in function_params(fn)
        if param in MOCKER_FIXTURES
    )
    return offences


class TestNoMockingLibrary:
    """testing.md §8: "no ``MagicMock``"; §3: doubles are real objects or hand-written fakes."""

    def test_no_test_module_imports_a_mocking_library_or_takes_mocker(self) -> None:
        """§8: no ``unittest.mock``, ``mock`` or ``pytest_mock`` import, no ``mocker`` fixture."""
        offences = _sweep(_mocking, python_files(TESTS_DIR))

        assert not offences, report("Mocking library used in tests", offences)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("from unittest.mock import MagicMock", True),
            ("from unittest import mock", True),
            ("import unittest.mock", True),
            ("import mock", True),
            ("def test_x(mocker): ...", True),
            ("from unittest import TestCase", False),
            ("def test_x(monkeypatch): ...", False),
        ],
    )
    def test_flags_every_spelling_of_a_mock(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_mocking, source, SNIPPET_TEST)) is flagged


def _sleeps(path: Path, tree: ast.Module) -> list[Offence]:
    delay_allowed = is_under(path, FAKEGATEWAY_DIR)
    return [
        Offence(path, call.lineno, ast.unparse(call))
        for call, name in calls(tree)
        if name == "time.sleep" or (name == "asyncio.sleep" and not delay_allowed and not _is_one_loop_turn(call))
    ]


def _is_one_loop_turn(call: ast.Call) -> bool:
    delays = [*call.args[:1], *(kw.value for kw in call.keywords if kw.arg == "delay")]
    return len(delays) == 1 and isinstance(delays[0], ast.Constant) and delays[0].value == 0


class TestNoSleepingForSynchronisation:
    """testing.md §4: "``await asyncio.sleep(0.1)`` is not synchronisation"; wait on an Event or future."""

    def test_no_test_sleeps_for_anything_but_one_loop_turn(self) -> None:
        """§4 and §8: only ``asyncio.sleep(0)`` is allowed; ``time.sleep`` never.

        ``tests/fakegateway/`` may ``asyncio.sleep``: its ``/control`` delay knob (§6) is behaviour, not
        synchronisation. ``time.sleep`` blocks the loop, so it is banned there too.
        """
        offences = _sweep(_sleeps, python_files(TESTS_DIR))

        assert not offences, report("Sleeps used for synchronisation", offences)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("import asyncio\nawait asyncio.sleep(0)", False),
            ("import asyncio\nawait asyncio.sleep(delay=0)", False),
            ("import asyncio\nawait asyncio.sleep(0.1)", True),
            ("import asyncio\nawait asyncio.sleep(delay)", True),
            ("import asyncio as aio\nawait aio.sleep(1)", True),
            ("from asyncio import sleep\nawait sleep(1)", True),
            ("from time import sleep\nsleep(0)", True),
            ("import time\ntime.sleep(0)", True),
        ],
    )
    def test_allows_only_a_zero_delay(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_sleeps, source, SNIPPET_TEST)) is flagged

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [("import asyncio\nawait asyncio.sleep(delay)", False), ("import time\ntime.sleep(0.1)", True)],
    )
    def test_lets_the_fake_gateway_delay_but_never_block(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_sleeps, source, FAKEGATEWAY_DIR / "gateway.py")) is flagged


def _unit_isolation(path: Path, tree: ast.Module) -> list[Offence]:
    offences = [
        Offence(path, line, f"imports {module}")
        for line, module in imported_modules(tree, path)
        if any(is_within(module, m) for m in UNIT_FORBIDDEN_MODULES)
    ]
    offences.extend(
        Offence(path, call.lineno, f"calls {name}")
        for call, name in calls(tree)
        if any(is_within(name, m) for m in UNIT_FORBIDDEN_MODULES)
    )
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name) | ast.Attribute(attr=name) if name in SOCKET_TEST_NAMES:
                offences.append(Offence(path, node.lineno, f"names {name}"))
            case ast.ImportFrom(names=names):
                offences.extend(
                    Offence(path, node.lineno, f"imports {a.name}") for a in names if a.name in SOCKET_TEST_NAMES
                )
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                offences.extend(
                    Offence(path, node.lineno, f"{node.name}() takes the {p!r} fixture")
                    for p in function_params(node)
                    if p in SOCKET_TEST_FIXTURES
                )
    return offences


class TestUnitTierIsolation:
    """testing.md §1: "A unit test that imports ``tests.fakegateway`` is an integration test in the wrong directory"."""

    def test_no_unit_test_reaches_the_fake_server_or_a_socket_harness(self) -> None:
        """§1 and §8: no ``tests.fakegateway``, websockets server, ``aiohttp.test_utils`` or ``TestServer`` under unit/."""
        offences = _sweep(_unit_isolation, _unit_test_files())

        assert not offences, report("Unit tests that leave the unit tier", offences)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("from tests.fakegateway import app", True),
            ("from tests import fakegateway", True),
            ("import tests.fakegateway.routes", True),
            ("from aiohttp import test_utils", True),
            ("from aiohttp.test_utils import TestServer", True),
            ("server = TestClient(app)", True),
            ("async def test_x(aiohttp_client): ...", True),
            ("from tests.unit._fakes import FakeGatewayTransport", False),
            ("from websockets.asyncio.server import serve", True),
            ("from websockets.asyncio import server", True),
            ("import websockets.server", True),
            ("from websockets import serve", True),
            ("import websockets\nwebsockets.serve(handler, '127.0.0.1', 0)", True),
            ("from websockets.sync.server import serve", True),
            ("from websockets.legacy.server import serve", True),
            ("from aiohttp import web\nrunner = web.AppRunner(app)", True),
            ("from aiohttp import web\nweb.run_app(app)", True),
            ("from aiohttp import web\nresponse = web.json_response({})", False),
            ("from aiohttp import ClientSession", False),
            ("from websockets.asyncio.client import connect", False),
        ],
    )
    def test_flags_the_fake_server_and_socket_harnesses(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_unit_isolation, source, SNIPPET_TEST)) is flagged


def _undocumented_regressions(path: Path, tree: ast.Module) -> list[Offence]:
    return [
        Offence(path, test.node.lineno, f"{test.node.name}() has no docstring")
        for test in collect_tests(path, tree)
        if test.marked_regression and not ast.get_docstring(test.node)
    ]


def _unmarked_regressions(path: Path, tree: ast.Module) -> list[Offence]:
    return [
        Offence(path, test.node.lineno, f"{test.node.name}() lacks @pytest.mark.regression")
        for test in collect_tests(path, tree)
        if not test.marked_regression and ("regression" in test.node.name or is_under(path, REGRESSION_DIR))
    ]


class TestRegressionContract:
    """testing.md §7: a regression test "is marked ``@pytest.mark.regression``" and "has a docstring"."""

    def test_every_marked_test_has_a_docstring(self) -> None:
        """§7.4: the docstring states what the code did wrong."""
        offences = _sweep(_undocumented_regressions, python_files(TESTS_DIR))

        assert not offences, report("Regression tests without a docstring", offences)

    def test_every_test_named_or_filed_as_one_carries_the_marker(self) -> None:
        """§7.2 and §7.5: a test named for a regression, or living in ``tests/regression/``, is marked."""
        offences = _sweep(_unmarked_regressions, python_files(TESTS_DIR))

        assert not offences, report("Regression tests without the marker", offences)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("@pytest.mark.regression\ndef test_x(): ...", True),
            ('@pytest.mark.regression\ndef test_x():\n    """Did wrong."""', False),
            ("@pytest.mark.regression\nclass TestX:\n    def test_x(self): ...", True),
            ("pytestmark = [pytest.mark.regression]\ndef test_x(): ...", True),
            ("class TestX:\n    pytestmark = pytest.mark.regression\n    def test_x(self): ...", True),
            ("from pytest import mark\n@mark.regression\ndef test_x(): ...", True),
            ("def test_x(): ...", False),
        ],
    )
    def test_requires_a_docstring_wherever_the_marker_comes_from(self, source: str, *, flagged: bool) -> None:
        full = f"import pytest\n{source}"

        assert bool(_snippet(_undocumented_regressions, full, SNIPPET_TEST)) is flagged

    @pytest.mark.parametrize(
        ("source", "path", "flagged"),
        [
            ("def test_regression_keeps_token(): ...", SNIPPET_TEST, True),
            ("def test_keeps_token(): ...", SNIPPET_REGRESSION_TEST, True),
            ("@pytest.mark.regression\ndef test_regression_keeps_token(): ...", SNIPPET_TEST, False),
            ("pytestmark = pytest.mark.regression\ndef test_keeps_token(): ...", SNIPPET_REGRESSION_TEST, False),
            ("def test_keeps_token(): ...", SNIPPET_TEST, False),
        ],
    )
    def test_requires_the_marker_by_name_or_directory(self, source: str, path: Path, *, flagged: bool) -> None:
        full = f"import pytest\n{source}"

        assert bool(_snippet(_unmarked_regressions, full, path)) is flagged


def _hosts_and_secrets(path: Path, tree: ast.Module) -> list[Offence]:
    return [Offence(path, line, reason) for line, text in string_literals(tree) if (reason := _secret_or_host(text))]


def _secret_or_host(text: str) -> str | None:
    """Why ``text`` is suspect, or ``None``.

    A secret-shaped run is 41+ base64/hex characters with a digit and one 20+ character unbroken segment,
    so long snake_case identifiers and test ids do not trip it; an ``AA:BB:…`` fingerprint never forms a run.
    """
    lowered = text.lower()
    if host := next((h for h in REAL_HOSTS if h in lowered), None):
        return f"names the real host {host} ({text[:60]!r})"
    if JWT_SHAPE.search(text):
        return "contains a JWT-shaped value"
    for run in SECRET_RUN.findall(text):
        if any(c.isdigit() for c in run) and SECRET_SEGMENT.search(run):
            return f"contains a secret-shaped run ({run[:12]}…, {len(run)} chars)"
    return None


def _fixture_file(path: Path, text: str) -> list[Offence]:
    offences = []
    for line, content in enumerate(text.splitlines(), start=1):
        if reason := _secret_or_host(content):
            offences.append(Offence(path, line, reason))
        offences.extend(
            Offence(path, line, f"carries {address}, outside loopback and the documentation ranges")
            for address in _addresses(content)
            if not any(address in network for network in FIXTURE_NETWORKS)
        )
    return offences


def _addresses(text: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every IPv4 (zero-padded octets included) and IPv6 address in ``text``; ``AA:BB:…`` fingerprints never parse."""
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
        ipaddress.IPv4Address(".".join(str(int(o)) for o in dotted.split(".")))
        for dotted in IPV4.findall(text)
        if all(int(o) <= 255 for o in dotted.split("."))
    ]
    for candidate in IPV6_CANDIDATE.findall(text):
        try:
            found.append(ipaddress.IPv6Address(candidate))
        except ValueError:
            continue
    return found


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


class TestNoRealHostsOrSecrets:
    """testing.md §5: "Fixture credentials are obviously fake"; hosts come from ``pyagora.const``."""

    def test_no_test_literal_names_a_real_host_or_looks_like_a_secret(self) -> None:
        """§5 and §8 ("no real hostnames in tests"): no Agora host, no JWT, no long base64/hex run.

        ``tests/meta/`` is exempt: it spells out the patterns it forbids.
        """
        offences = _sweep(_hosts_and_secrets, python_files(TESTS_DIR, exclude=[META_DIR]))

        assert not offences, report("Test literals carrying a real host or secret-shaped value", offences)

    def test_no_fixture_carries_a_real_host_address_or_secret(self) -> None:
        """§6 and Constitution §7: fixtures are redacted, so no Agora host and only loopback/RFC 5737/3849 addresses."""
        offences = []
        for path in _fixture_files():
            try:
                offences.extend(_fixture_file(path, path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                offences.append(Offence(path, 1, "is not UTF-8 text; fixtures are readable recordings"))

        assert not offences, report("Fixtures carrying a real host, address or secret-shaped value", offences)

    @pytest.mark.parametrize(
        ("text", "suspect"),
        [
            ("not-a-real-secret", False),
            ("test_returns_cached_token_when_fresh_and_the_lock_is_free", False),
            ("test_v2_join_retries_4_times_then_raises_join_timeout_error", False),
            ("A" * 45, False),
            ("abc1-def2-ghi3-jkl4-mno5-pqr6-stu7-vwx8-yz90", False),
            (f"https://webrtc2-ap-web-1.{REAL_HOSTS[0]}", True),
            (f"https://API.{REAL_HOSTS[1].upper()}/dev/v2", True),
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", True),
            ("0123456789abcdef0123456789abcdef0123456789abcdef", True),
        ],
    )
    def test_flags_real_hosts_and_secret_shaped_literals(self, text: str, *, suspect: bool) -> None:
        assert bool(_snippet(_hosts_and_secrets, repr(text), SNIPPET_TEST)) is suspect

    @pytest.mark.parametrize(
        ("text", "suspect"),
        [
            ("a=fingerprint:sha-256 " + ":".join(["0F", "3A"] * 16), False),
            ("a=candidate:1 1 udp 2122260223 192.0.2.10 54321 typ host", False),
            ('{"ip": "127.0.0.1", "port": 4001}', False),
            ("c=IN IP4 0.0.0.0", False),
            ("a=ice-pwd:not-a-real-ice-password", False),
            ("a=fingerprint:sha-256 " + "0F3A" * 16, True),
            ('"token": "006' + "a1B2" * 12 + '"', True),
            ("a=candidate:1 1 udp 2122260223 203.0.114.7 54321 typ host", True),
            ('{"ip": "10.0.0.8"}', True),
            ("host 10.0.0.8.", True),
            ("host 010.000.000.008", True),
            ("a=candidate:2 1 udp 2122262783 2001:4860:4860::8888 54322 typ host", True),
            ("a=candidate:2 1 udp 2122262783 2001:db8::7 54322 typ host", False),
            ("c=IN IP6 ::1", False),
            ("sdk 4.23.999.1", False),
            (f'{{"edge": "edge-1.{REAL_HOSTS[0]}"}}', True),
        ],
    )
    def test_flags_fixture_lines_but_not_fingerprints_or_documentation_addresses(
        self, text: str, *, suspect: bool
    ) -> None:
        assert bool(_fixture_file(FIXTURES_DIR / "sdp" / "snippet.sdp", text)) is suspect


def _misplaced_fakes(path: Path, tree: ast.Module) -> list[Offence]:
    return [
        Offence(path, node.lineno, f"defines {node.name}")
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and FAKE_CLASS.match(node.name)
    ]


def _duplicated_builders(files: Iterable[tuple[Path, ast.Module]]) -> list[Offence]:
    homes: dict[str, list[Offence]] = {}
    for path, tree in files:
        for fn in top_level_defs(tree):
            if BUILDER.fullmatch(fn.name):
                homes.setdefault(fn.name.lstrip("_"), []).append(Offence(path, fn.lineno, f"defines {fn.name}()"))
    return [o for defs in homes.values() if len({d.path for d in defs}) > 1 for o in defs]


class TestFakesLiveInOnePlace:
    """testing.md §2: fakes live in ``tests/unit/_fakes.py``, builders in one ``_helpers.py``."""

    def test_no_fake_class_is_defined_outside_the_fakes_module(self) -> None:
        """§2: ``Fake*`` classes live in ``tests/unit/_fakes.py``; the fake gateway is its own thing."""
        home = UNIT_DIR / "_fakes.py"
        offences = _sweep(_misplaced_fakes, python_files(TESTS_DIR, exclude=[FAKEGATEWAY_DIR, home]))

        assert not offences, report(f"Fakes defined outside {rel(home)} and {rel(FAKEGATEWAY_DIR)}/", offences)

    def test_no_builder_is_defined_in_more_than_one_module(self) -> None:
        """§2: "Four copies of ``make_join_ok`` is the failure this rule exists to stop".

        Scans every tier (not ``tests/meta/``), so a module shadowing a shared builder is caught wherever it sits.
        """
        files = [(p, parse(p)) for p in python_files(TESTS_DIR, exclude=[META_DIR])]
        duplicated = _duplicated_builders(files)

        assert not duplicated, report("Builders defined in more than one module (move to _helpers.py)", duplicated)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("class FakeClock: ...", True),
            ("class _FakeClock: ...", True),
            ("def f():\n    class FakeX: ...", True),
            ("class Clock: ...", False),
        ],
    )
    def test_flags_fake_classes_at_any_depth(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_misplaced_fakes, source, SNIPPET_TEST)) is flagged

    @pytest.mark.parametrize(
        ("first", "second", "flagged"),
        [
            ("def make_join_ok(): ...", "def make_join_ok(): ...", True),
            ("def _make_join_ok(): ...", "def make_join_ok(): ...", True),
            ("class TestX:\n    def make_join_ok(self): ...", "def make_join_ok(): ...", True),
            ("def test_a():\n    def make_join_ok(): ...", "def make_join_ok(): ...", False),
            ("def make_join_ok(): ...", "def make_ap_response(): ...", False),
        ],
    )
    def test_flags_a_builder_defined_twice(self, first: str, second: str, *, flagged: bool) -> None:
        files = [(UNIT_DIR / "a.py", ast.parse(first)), (UNIT_DIR / "b.py", ast.parse(second))]

        assert bool(_duplicated_builders(files)) is flagged


def _layer_rule(importer: Path, module: str) -> str | None:
    """Why ``importer`` may not import ``module``, or ``None`` if the layer map allows it."""
    top = module.split(".", maxsplit=1)[0]
    if top in FORBIDDEN_EVERYWHERE:
        return f"imports {module}: the package knows no host, vendor library or test code"
    if module == PACKAGE and importer != PACKAGE_DIR / "__init__.py":
        return f"imports the package root {PACKAGE}; import the defining module"
    parts = importer.relative_to(PACKAGE_DIR).parts
    layer = parts[0] if len(parts) > 1 else importer.stem
    if (allowed := LAYER_RULES.get(layer)) is None:
        return None
    if top != PACKAGE:
        return _third_party_rule(layer, module)
    if any(is_within(module, f"{PACKAGE}.{name}") for name in (layer, *allowed)):
        return None
    return f"{layer} imports {module}"


def _third_party_rule(layer: str, module: str) -> str | None:
    top = module.split(".", maxsplit=1)[0]
    permitted = THIRD_PARTY_ALLOWED.get(layer)
    if permitted is None or top in sys.stdlib_module_names or top in permitted:
        return None
    return f"{layer}/ imports {module}: only the standard library and {', '.join(permitted)}"


def _layer_violations(path: Path, tree: ast.Module) -> list[Offence]:
    return [
        Offence(path, line, reason)
        for line, module in imported_modules(tree, path)
        if (reason := _layer_rule(path, module))
    ]


class TestLayerDirection:
    """Constitution §3: "Nothing imports upward"; architecture.md §1 is the layer map."""

    def test_no_package_module_imports_against_the_layer_map(self) -> None:
        """§1: sdp ← ap, rtm ← session; ``TYPE_CHECKING`` imports count as dependencies."""
        offences = _sweep(_layer_violations, _source_modules())

        assert not offences, report("Imports that point the wrong way", offences)

    def test_every_top_level_module_and_package_has_a_layer(self) -> None:
        """§1: an unmapped layer would import anything unchecked, so every one must be in ``LAYER_RULES``."""
        layers = {
            p.stem
            for p in PACKAGE_DIR.iterdir()
            if (p.suffix == ".py" or (p / "__init__.py").is_file()) and not p.name.startswith("_")
        }

        assert layers, f"no layers found under {rel(PACKAGE_DIR)}"
        assert not layers - LAYER_RULES.keys(), (
            f"layers missing from LAYER_RULES: {sorted(layers - LAYER_RULES.keys())}"
        )

    @pytest.mark.parametrize(
        ("importer", "module", "allowed"),
        [
            ("const.py", "pyagora.models", False),
            ("exceptions.py", "pyagora.models", False),
            ("models.py", "pyagora.exceptions", True),
            ("models.py", "pyagora.sdp", False),
            ("sdp/offer.py", "pyagora.models", True),
            ("sdp/offer.py", "pyagora.exceptions", True),
            ("sdp/offer.py", "pyagora.sdp.candidates", True),
            ("sdp/offer.py", "pyagora.const", True),
            ("sdp/offer.py", "pyagora.ap.response", False),
            ("sdp/offer.py", "pyagora.session.messages", False),
            ("sdp/offer.py", "sdp_transform", True),
            ("sdp/offer.py", "collections.abc", True),
            ("sdp/offer.py", "aiohttp", False),
            ("sdp/answer.py", "websockets.asyncio.client", False),
            ("ap/client.py", "pyagora.const", True),
            ("ap/client.py", "aiohttp", True),
            ("ap/client.py", "pyagora.sdp", False),
            ("ap/client.py", "pyagora.rtm.client", False),
            ("ap/client.py", "pyagora.session.transport", False),
            ("rtm/client.py", "pyagora.models", True),
            ("rtm/client.py", "pyagora.ap.response", False),
            ("rtm/client.py", "pyagora.session", False),
            ("session/session.py", "pyagora.sdp.offer", True),
            ("session/session.py", "pyagora.ap.response", True),
            ("session/session.py", "pyagora.const", True),
            ("session/session.py", "pyagora.rtm.client", False),
            ("session/transport.py", "websockets.asyncio.client", True),
            ("session/session.py", "pyagora", False),
            ("models.py", "pyagora", False),
            ("__init__.py", "pyagora.session.session", True),
            ("session/session.py", "webrtc_models", False),
            ("__init__.py", "homeassistant.core", False),
            ("ap/client.py", "pypetkitapi.client", False),
            ("rtm/client.py", "paho.mqtt.client", False),
            ("rtm/client.py", "aiofiles", False),
            ("session/session.py", "go2rtc_client", False),
            ("sdp/offer.py", "pymammotion.data", False),
            ("session/session.py", "tests.unit._fakes", False),
        ],
    )
    def test_applies_the_layer_map(self, importer: str, module: str, *, allowed: bool) -> None:
        assert (_layer_rule(PACKAGE_DIR / importer, module) is None) is allowed

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("if TYPE_CHECKING:\n    from pyagora.session import transport", True),
            ("from pyagora.session import GatewayTransport", True),
            ("from ..session import transport", True),
            ("from pyagora import ChannelCredentials", True),
            ("from pyagora import models", False),
            ("from ..models import IceCandidate", False),
            ("from .response import APResponse", False),
        ],
    )
    def test_resolves_type_checking_relative_and_submodule_imports(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_layer_violations, source, PACKAGE_DIR / "ap" / "client.py")) is flagged


def _local_imports(path: Path, tree: ast.Module) -> list[Offence]:
    offences = {
        (node.lineno, ast.unparse(node)): Offence(path, node.lineno, f"{ast.unparse(node)} inside {fn.name}()")
        for fn in functions(tree)
        for stmt in fn.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Import | ast.ImportFrom)
    }
    return list(offences.values())


class TestTopLevelImportsOnly:
    """code_style.md: "Top-level imports only. No imports inside functions"."""

    def test_no_package_function_contains_an_import(self) -> None:
        """code_style.md Structure: circular-import pressure is solved with ``TYPE_CHECKING``, not local imports."""
        offences = _sweep(_local_imports, _source_modules())

        assert not offences, report("Imports inside function bodies", offences)

    @pytest.mark.parametrize(
        ("source", "count"),
        [
            ("def f():\n    import os", 1),
            ("class C:\n    async def f(self):\n        def g():\n            from os import path", 1),
            ("import os\nif TYPE_CHECKING:\n    from os import path", 0),
        ],
    )
    def test_flags_each_import_inside_a_function_once(self, source: str, count: int) -> None:
        assert len(_snippet(_local_imports, source, PACKAGE_DIR / "session" / "session.py")) == count


class TestPublicSurface:
    """Constitution §10: "``pyagora.__all__`` lists the supported API"."""

    def test_every_listed_name_is_importable_from_the_package(self) -> None:
        """§10: a name in ``__all__`` that the package cannot hand out is a broken promise."""
        package = importlib.import_module(PACKAGE)
        listed = getattr(package, "__all__", None)

        assert listed is not None, f"{PACKAGE}/__init__.py defines no __all__"
        missing = [name for name in listed if not hasattr(package, name)]
        assert not missing, report(f"Names in {PACKAGE}.__all__ the package does not export", missing)

    def test_all_is_sorted_without_duplicates(self) -> None:
        """§10: the surface is deliberate, so it is kept in one reviewable order."""
        listed = list(getattr(importlib.import_module(PACKAGE), "__all__", []))

        assert listed == sorted(set(listed)), (
            f"{PACKAGE}.__all__ is not sorted and unique; expected {sorted(set(listed))}"
        )


def _hand_driven_loops(path: Path, tree: ast.Module) -> list[Offence]:
    aliases = import_aliases(tree)
    offences = [
        Offence(path, call.lineno, f"calls {name}")
        for call, name in calls(tree)
        if name in EVENT_LOOP_CALLS or name.endswith(".run_until_complete")
    ]
    offences.extend(
        Offence(path, fn.lineno, f"{fn.name}() carries @pytest.mark.asyncio (asyncio_mode is auto)")
        for fn in functions(tree)
        for decorator in fn.decorator_list
        if _is_asyncio_marker(decorator, aliases)
    )
    return offences


def _is_asyncio_marker(expr: ast.expr, aliases: dict[str, str]) -> bool:
    target = expr.func if isinstance(expr, ast.Call) else expr
    return qualified_name(target, aliases) == "pytest.mark.asyncio"


class TestAsyncTests:
    """testing.md §4: "``asyncio_mode = "auto"``; no ``@pytest.mark.asyncio`` decorators"."""

    def test_no_test_drives_its_own_event_loop(self) -> None:
        """§4: async tests are ``async def``; no ``asyncio.run``, ``get_event_loop``, ``run_until_complete``.

        A package ``__main__.py`` (the fake gateway's standalone runner) is an entry point, not a test.
        """
        files = [f for f in python_files(TESTS_DIR, exclude=[META_DIR]) if f.name != "__main__.py"]
        offences = _sweep(_hand_driven_loops, files)

        assert not offences, report("Tests driving their own event loop", offences)

    @pytest.mark.parametrize(
        ("source", "flagged"),
        [
            ("import asyncio\nasyncio.run(main())", True),
            ("from asyncio import run\nrun(main())", True),
            ("import asyncio\nasyncio.get_event_loop().run_until_complete(main())", True),
            ("def f(loop):\n    loop.run_until_complete(main())", True),
            ("import pytest\n@pytest.mark.asyncio\nasync def test_x(): ...", True),
            ("import asyncio\nasync def test_x():\n    await asyncio.wait_for(main(), 1)", False),
            ("import pytest\n@pytest.mark.parametrize('a', [1])\nasync def test_x(a): ...", False),
        ],
    )
    def test_flags_hand_driven_loops(self, source: str, *, flagged: bool) -> None:
        assert bool(_snippet(_hand_driven_loops, source, SNIPPET_TEST)) is flagged


def test_the_checks_walk_the_real_tree() -> None:
    """Guards every sweep above against passing vacuously on a mislocated root."""
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert _source_modules(), f"no source modules found under {rel(PACKAGE_DIR)}"
    assert _unit_test_files(), f"no unit-test files found under {rel(UNIT_DIR)}"
    assert _fixture_files(), f"no fixture files found under {rel(FIXTURES_DIR)}"
