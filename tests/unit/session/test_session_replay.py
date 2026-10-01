"""``AgoraSession`` replaying the Mammotion sessions captured on 2026-10-01 (``tests/fixtures/sessions/``).

Each recorded inbound frame is fed verbatim, in order; the session's request ids come from the
recording, so every response correlates as it did live. The test asserts the session sends the
recorded outbound frames in the recorded order and ends for the recorded reason, and that every recorded
reply (join result, subscribe ack, ping reply) answers a request the session is tracking. A recording that
ends with its viewers still joined is checked at its last frame, then closed outside the recording.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import itertools
from typing import TYPE_CHECKING, Any

import pytest

from pyagorartc.ap import APResponse
from pyagorartc.const import EDGE_DOMAIN_SUFFIX, PING_INTERVAL_S
from pyagorartc.models import CloseReason, SessionOptions
from pyagorartc.session import AgoraSession
from tests._helpers import CREDENTIALS, load_fixture, load_json_fixture
from tests.unit._fakes import FakeGatewayConnection, FakeGatewayTransport, ManualClock, ManualSleep, Recorder

if TYPE_CHECKING:
    from collections.abc import Coroutine

OFFER = load_fixture("sdp/chrome_recvonly_offer.sdp")
TIMEOUT = 1.0
# The captured joins carried a license (the Mammotion token response has one).
REPLAY_CREDENTIALS = replace(CREDENTIALS, license="license-not-real")


@dataclass
class Replayed:
    """One recorded viewer session and the ``AgoraSession`` replaying it."""

    entries: list[dict[str, Any]]
    target_uid: int
    edge_offset: int = 0
    conn: FakeGatewayConnection = field(default_factory=FakeGatewayConnection)
    transport: FakeGatewayTransport | None = None
    joined_at_last_frame: bool | None = None
    closed: Recorder = field(default_factory=Recorder)
    sleep: ManualSleep = field(default_factory=lambda: ManualSleep(ManualClock()))
    join: asyncio.Task[str] | None = None
    session: AgoraSession | None = None

    def gateway(self, direction: str) -> list[dict[str, Any]]:
        return [e["frame"] for e in self.entries if e["source"] == "gateway" and e["direction"] == direction]

    def start(self) -> None:
        ap_in = next(e["frame"] for e in self.entries if e["source"] == "ap" and e["direction"] == "in")
        # Ids past the recording are only drawn by the leave that closes a still-joined recording.
        ids = itertools.chain([f["_id"] for f in self.gateway("out")], (f"after{n}" for n in itertools.count()))
        self.transport = FakeGatewayTransport(self.conn)
        self.session = AgoraSession(
            REPLAY_CREDENTIALS,
            APResponse.from_api_response(ap_in),
            options=SessionOptions(target_uid=self.target_uid, gateway_edge_offset=self.edge_offset),
            transport=self.transport,
            on_closed=self.closed,
            clock=self.sleep.clock,
            sleep=self.sleep,
            request_id_factory=lambda: next(ids),
        )
        self.join = asyncio.ensure_future(self.session.join(OFFER, "session-test"))

    async def expect_sent(self, recorded: dict[str, Any]) -> dict[str, Any]:
        """Drive the session to its next send and return it; a ping is due once the ping interval passes."""
        index = len(self.conn.sent)
        if recorded["_type"] == "ping":
            await asyncio.wait_for(self.sleep.wait_for_sleepers(1), TIMEOUT)
            self.sleep.advance(PING_INTERVAL_S)
        elif recorded["_type"] == "leave":
            assert self.session is not None
            await asyncio.wait_for(self.session.close(), TIMEOUT)
        await asyncio.wait_for(self.conn.wait_for_sent(index + 1), TIMEOUT)
        return self.conn.sent[index]

    def recorded_edge_url(self) -> str:
        """The URL of the edge the recording says this viewer was on (``edge``, ``ip:port``)."""
        ip, port = next(e["edge"] for e in self.entries if e["source"] == "gateway").split(":")
        return f"wss://{ip.replace('.', '-')}{EDGE_DOMAIN_SUFFIX}:{port}"


async def _first_of(*waits: Coroutine[Any, Any, None]) -> None:
    """Return once any of ``waits`` resolves (within ``TIMEOUT``), cancelling the rest."""
    tasks = [asyncio.ensure_future(w) for w in waits]
    done, pending = await asyncio.wait(tasks, timeout=TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    assert done, f"none of {len(tasks)} waits resolved within {TIMEOUT}s"


def _sessions(name: str, targets: dict[str | None, int]) -> dict[str | None, Replayed]:
    entries = load_json_fixture(f"sessions/{name}.json")
    offsets = EDGE_OFFSETS.get(name, {})
    return {
        who: Replayed([e for e in entries if e.get("session") == who], uid, offsets.get(who, 0))
        for who, uid in targets.items()
    }


async def replay(name: str, targets: dict[str | None, int]) -> dict[str | None, Replayed]:
    """Play ``sessions/<name>.json`` in recorded order through one ``AgoraSession`` per recorded viewer."""
    sessions = _sessions(name, targets)
    entries = load_json_fixture(f"sessions/{name}.json")
    for entry in (e for e in entries if e["source"] == "gateway"):
        replayed = sessions[entry.get("session")]
        frame = entry["frame"]
        if entry["direction"] == "in":
            replayed.conn.feed(frame)
            continue
        if frame["_type"] == "join_v3":
            replayed.start()
        sent = await replayed.expect_sent(frame)
        assert (sent["_id"], sent["_type"]) == (frame["_id"], frame["_type"])
        assert sorted(sent.get("_message", {})) == sorted(frame.get("_message", {}))
        if frame["_type"] == "subscribe":
            assert sent["_message"] == frame["_message"]
    for replayed in sessions.values():
        assert replayed.join is not None
        assert replayed.session is not None
        await asyncio.wait_for(asyncio.shield(replayed.join), TIMEOUT)
        if name in STILL_JOINED:
            await _first_of(replayed.conn.wait_until_read(), replayed.closed.wait_for(1))
            replayed.joined_at_last_frame = replayed.session.is_joined and not replayed.closed.calls
        else:
            await asyncio.wait_for(replayed.closed.wait_for(1), TIMEOUT)
        sent = [(f["_id"], f["_type"]) for f in replayed.conn.sent]
        assert sent == [(f["_id"], f["_type"]) for f in replayed.gateway("out")]
    for replayed in sessions.values():
        if name in STILL_JOINED:
            assert replayed.session is not None
            await asyncio.wait_for(replayed.session.close(), TIMEOUT)
    return sessions


#: Each scenario's recorded viewers and the camera uid each targeted (from the capture's session log).
SCENARIOS: dict[str, dict[str | None, int]] = {
    "luba2_single": {None: 2},
    "luba3_vision": {None: 1},
    "luba2_left_then_right": {"left": 1, "right": 2},
    "luba2_racing_pair": {"left": 1, "right": 2},
    "luba2_two_edges": {"left": 1, "right": 2},
}
#: Viewers the host put on a later AP edge with ``gateway_edge_offset`` (Q20, D33).
EDGE_OFFSETS: dict[str, dict[str | None, int]] = {"luba2_two_edges": {"right": 1}}
#: Recordings whose frames end with every viewer still joined.
STILL_JOINED = frozenset({"luba2_two_edges"})
VIEWERS = [(name, who) for name, targets in SCENARIOS.items() for who in targets]


def _recorded_join_result(replayed: Replayed) -> dict[str, Any]:
    return next(f for f in replayed.gateway("in") if "ortc" in f.get("_message", {}))


class TestReplayedSessions:
    async def test_luba2_single_camera_subscribes_its_target_and_ends_when_the_host_closes(self) -> None:
        (only,) = (await replay("luba2_single", SCENARIOS["luba2_single"])).values()

        assert only.closed.calls == [CloseReason.CLOSED_BY_HOST]
        assert [f["_message"]["stream_id"] for f in only.conn.sent_of_type("subscribe")] == [2]

    async def test_luba3_vision_camera_subscribes_uid_1_and_not_uid_2_that_never_publishes(self) -> None:
        (only,) = (await replay("luba3_vision", SCENARIOS["luba3_vision"])).values()

        assert only.closed.calls == [CloseReason.CLOSED_BY_HOST]
        assert [f["_message"]["stream_id"] for f in only.conn.sent_of_type("subscribe")] == [1]

    async def test_luba2_right_joining_on_the_same_uid_evicts_the_left_with_a_gateway_quit(self) -> None:
        sessions = await replay("luba2_left_then_right", SCENARIOS["luba2_left_then_right"])

        assert sessions["left"].closed.calls == [CloseReason.GATEWAY_QUIT]
        assert sessions["left"].conn.sent_of_type("leave") == []
        assert sessions["right"].closed.calls == [CloseReason.CLOSED_BY_HOST]

    async def test_luba2_racing_pair_both_survive_and_each_subscribes_its_own_camera(self) -> None:
        sessions = await replay("luba2_racing_pair", SCENARIOS["luba2_racing_pair"])

        closed = {who: s.closed.calls for who, s in sessions.items()}
        subscribed = {
            who: [f["_message"]["stream_id"] for f in s.conn.sent_of_type("subscribe")] for who, s in sessions.items()
        }
        assert closed == {"left": [CloseReason.CLOSED_BY_HOST], "right": [CloseReason.CLOSED_BY_HOST]}
        assert subscribed == {"left": [1], "right": [2]}

    async def test_luba2_two_edges_puts_the_right_on_the_aps_second_edge_with_offset_1(self) -> None:
        sessions = await replay("luba2_two_edges", SCENARIOS["luba2_two_edges"])

        right = sessions["right"]
        assert right.transport is not None
        ap_in = next(e["frame"] for e in right.entries if e["source"] == "ap" and e["direction"] == "in")
        second = APResponse.from_api_response(ap_in).get_gateway_addresses()[1]
        assert right.transport.urls == [right.recorded_edge_url()]
        assert right.recorded_edge_url() == f"wss://{second.ip.replace('.', '-')}{EDGE_DOMAIN_SUFFIX}:{second.port}"

    async def test_luba2_two_edges_left_stays_on_the_aps_first_edge(self) -> None:
        left = (await replay("luba2_two_edges", SCENARIOS["luba2_two_edges"]))["left"]

        assert left.transport is not None
        assert left.transport.urls == [left.recorded_edge_url()]

    async def test_luba2_same_uid_on_two_edges_neither_is_evicted_and_each_subscribes_its_camera(self) -> None:
        """Q20: no 2003 quit for either viewer; both still joined 44 s after the second ``join_v3``."""
        sessions = await replay("luba2_two_edges", SCENARIOS["luba2_two_edges"])

        joined = {who: s.joined_at_last_frame for who, s in sessions.items()}
        subscribed = {
            who: [f["_message"]["stream_id"] for f in s.conn.sent_of_type("subscribe")] for who, s in sessions.items()
        }
        assert joined == {"left": True, "right": True}
        assert subscribed == {"left": [1], "right": [2]}


class TestReplayedResponses:
    @pytest.mark.parametrize("name", list(SCENARIOS))
    async def test_every_recorded_reply_answers_a_request_the_session_is_tracking(
        self, name: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        await replay(name, SCENARIOS[name])

        assert [
            record.getMessage() for record in caplog.records if "matches no pending request" in record.getMessage()
        ] == []


class TestReplayedJoin:
    @pytest.mark.parametrize(("name", "who"), VIEWERS)
    async def test_sends_the_recorded_ap_response_and_user_attributes(self, name: str, who: str | None) -> None:
        replayed = (await replay(name, SCENARIOS[name]))[who]

        sent = replayed.conn.sent[0]["_message"]
        recorded = replayed.gateway("out")[0]["_message"]
        assert (sent["ap_response"], sent["attributes"]) == (recorded["ap_response"], recorded["attributes"])

    @pytest.mark.parametrize(("name", "who"), VIEWERS)
    async def test_answers_active_with_the_gateways_ice_and_dtls_parameters(self, name: str, who: str | None) -> None:
        replayed = (await replay(name, SCENARIOS[name]))[who]

        assert replayed.join is not None
        answer = replayed.join.result()
        ortc = _recorded_join_result(replayed)["_message"]["ortc"]
        assert "a=setup:active" in answer  # the captured gateway role is "client" (D5)
        assert f"a=ice-ufrag:{ortc['iceParameters']['iceUfrag']}" in answer
        assert f"a=fingerprint:sha-256 {ortc['dtlsParameters']['fingerprints'][0]['fingerprint']}" in answer
