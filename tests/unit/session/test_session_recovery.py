from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyagorartc.const import (
    PEER_RECOVER_COOLDOWN_S,
    PEER_RECOVER_MAX_ATTEMPTS,
    PEER_REJOIN_DEBOUNCE_S,
)
from pyagorartc.models import SessionOptions
from tests._helpers import load_json_fixture
from tests.unit.session._helpers import (
    MARKER_UID,
    OTHER_PUBLISHER,
    PUBLISHER,
    TIMEOUT,
    Recorder,
    announced,
    event,
    rig,
)

if TYPE_CHECKING:
    from tests.unit.session._helpers import Rig


class TestPeerRecovery:
    async def depart(self, r: Rig) -> None:
        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))
        await r.sent_type("unsubscribe", len(r.conn.sent_of_type("unsubscribe")) + 1)

    async def return_(self, r: Rig) -> None:
        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.sent_type("subscribe", len(r.subscribes()) + 1)

    async def recover_once(self, r: Rig, peer_left: Recorder) -> None:
        await self.depart(r)
        await r.sleepers(2)
        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await asyncio.wait_for(peer_left.wait_for(len(peer_left.calls) + 1), TIMEOUT)

    async def test_asks_the_host_to_recover_a_peer_still_gone_after_the_debounce(self) -> None:
        peer_left = Recorder()
        r = rig(on_peer_left=peer_left)
        await r.join(extra=announced())
        await self.depart(r)
        await r.sleepers(2)

        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)

        await asyncio.wait_for(peer_left.wait_for(1), TIMEOUT)
        assert peer_left.calls == [PUBLISHER]

    async def test_does_nothing_when_the_peer_rejoins_within_the_debounce(self) -> None:
        peer_left = Recorder()
        r = rig(on_peer_left=peer_left)
        await r.join(extra=announced())
        await self.depart(r)
        await self.return_(r)

        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await r.mark()

        assert peer_left.calls == []

    async def test_skips_a_second_departure_inside_the_cooldown(self) -> None:
        peer_left = Recorder()
        r = rig(on_peer_left=peer_left)
        await r.join(extra=announced())
        await self.recover_once(r, peer_left)
        await self.return_(r)

        await self.depart(r)
        await r.mark()
        assert r.sleep.pending == 1  # the ping loop only: no recovery timer is armed
        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await r.mark(MARKER_UID + 1)

        assert peer_left.calls == [PUBLISHER]

    async def test_stops_recovering_after_the_attempt_cap(self) -> None:
        peer_left = Recorder()
        r = rig(on_peer_left=peer_left)
        await r.join(extra=announced())
        for _ in range(PEER_RECOVER_MAX_ATTEMPTS):
            await self.recover_once(r, peer_left)
            await self.return_(r)
            r.sleep.advance(PEER_RECOVER_COOLDOWN_S)

        await self.depart(r)
        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await r.mark()

        assert len(peer_left.calls) == PEER_RECOVER_MAX_ATTEMPTS

    async def test_arms_no_timer_without_an_on_peer_left_callback(self) -> None:
        r = rig()
        await r.join(extra=announced())

        await self.depart(r)
        await r.mark()

        assert r.sleep.pending == 1  # the ping loop only

    async def test_a_raising_on_peer_left_leaves_the_session_running(self) -> None:
        peer_left = Recorder(error=RuntimeError("host recovery failed"))
        r = rig(on_peer_left=peer_left)
        await r.join(extra=announced())

        await self.recover_once(r, peer_left)
        await r.mark()

        assert r.session.is_joined

    async def test_only_the_latest_departure_is_recovered(self) -> None:
        peer_left = Recorder()
        r = rig(on_peer_left=peer_left)
        await r.join(extra=announced())
        r.conn.feed(event("on_user_online", uid=OTHER_PUBLISHER))
        await self.depart(r)
        r.conn.feed(event("on_user_offline", uid=OTHER_PUBLISHER))
        await r.mark()

        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await asyncio.wait_for(peer_left.wait_for(1), TIMEOUT)
        await r.mark(MARKER_UID + 1)

        assert peer_left.calls == [OTHER_PUBLISHER]

    @pytest.mark.regression
    async def test_a_departure_while_the_host_recovers_does_not_cancel_the_recovery(self) -> None:
        """A second departure cancelled the recovery task even after the debounce, killing the host's ``on_peer_left``."""
        started = asyncio.Event()
        release = asyncio.Event()
        settled = asyncio.Event()
        outcome: list[str] = []

        async def recovering(_uid: int) -> None:
            started.set()
            try:
                await release.wait()
                outcome.append("finished")
            except asyncio.CancelledError:
                outcome.append("cancelled")
                raise
            finally:
                settled.set()

        r = rig(on_peer_left=recovering)
        await r.join(extra=announced())
        r.conn.feed(event("on_user_online", uid=OTHER_PUBLISHER))
        await self.depart(r)
        await r.sleepers(2)
        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await asyncio.wait_for(started.wait(), TIMEOUT)
        r.conn.feed(event("on_user_offline", uid=OTHER_PUBLISHER))
        await r.mark()

        release.set()

        await asyncio.wait_for(settled.wait(), TIMEOUT)
        assert outcome == ["finished"]

    async def test_ignores_the_departure_of_a_uid_other_than_the_target(self) -> None:
        peer_left = Recorder()
        r = rig(SessionOptions(target_uid=OTHER_PUBLISHER), on_peer_left=peer_left)
        await r.join()

        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))
        r.sleep.advance(PEER_REJOIN_DEBOUNCE_S)
        await r.mark(OTHER_PUBLISHER)

        assert peer_left.calls == []
