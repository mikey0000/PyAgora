"""Subscribing to the device's stream against the fake Agora: listed at join, announced later, filtered, lost."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pyagora.const import PEER_REJOIN_DEBOUNCE_S
from pyagora.models import RemoteStream, SessionOptions
from tests.fakegateway._common import DEVICE_SSRC, DEVICE_UID
from tests.integration._helpers import SESSION_TIMEOUT_S
from tests.unit._fakes import Recorder

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import SessionRig

OTHER_UID = DEVICE_UID + 1


class TestSubscribe:
    async def test_subscribes_to_a_stream_listed_in_the_join_with_the_option_codec_and_no_rtx(
        self, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session(SessionOptions(client_codec="h264"))

        await r.join()

        (frame,) = await r.received("subscribe")
        message = frame["_message"]
        assert (message["stream_id"], message["ssrcId"], message["codec"]) == (DEVICE_UID, DEVICE_SSRC, "h264")
        assert message["rtx"] is False  # the fake's ORTC lists no rtx codec, like the live gateway

    async def test_subscribes_once_a_device_joining_later_is_both_online_and_publishing(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(device_online=False, peer_online=True)
        r = new_session()

        await r.join()

        (frame,) = await r.received("subscribe")
        assert frame["_message"]["stream_id"] == DEVICE_UID

    async def test_subscribes_when_the_stream_is_announced_before_the_publisher(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(device_online=False)
        r = new_session()
        await r.join()

        await fake_agora.announce_peer(stream_first=True)

        (frame,) = await r.received("subscribe")
        assert frame["_message"]["stream_id"] == DEVICE_UID

    async def test_reports_the_stream_to_the_host(self, new_session: Callable[..., SessionRig]) -> None:
        on_stream = Recorder()
        r = new_session(on_stream=on_stream)

        await r.join()

        await asyncio.wait_for(on_stream.wait_for(1), SESSION_TIMEOUT_S)
        (stream,) = on_stream.calls
        assert isinstance(stream, RemoteStream)
        assert (stream.uid, stream.ssrc) == (DEVICE_UID, DEVICE_SSRC)

    async def test_does_not_subscribe_to_a_publisher_other_than_the_target(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(device_online=False)
        r = new_session(SessionOptions(target_uid=OTHER_UID))
        await r.join()

        await fake_agora.announce_peer()
        await fake_agora.send_token_will_expire()
        # The renew is spawned after any subscribe the announcement would have spawned, so it arrives after it.
        await r.received("renew_token")

        assert fake_agora.state.log.received_of_type("subscribe") == []
        assert (r.session.remote_streams, r.session.remote_users) == ((), frozenset())

    async def test_subscribes_to_the_target_publisher(self, new_session: Callable[..., SessionRig]) -> None:
        r = new_session(SessionOptions(target_uid=DEVICE_UID))

        await r.join()

        (frame,) = await r.received("subscribe")
        assert frame["_message"]["stream_id"] == DEVICE_UID


class TestPeerLeaves:
    async def test_unsubscribes_and_tells_the_host_once_after_the_debounce(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        peer_left = Recorder()
        r = new_session(on_peer_left=peer_left)
        await r.join()
        await r.received("subscribe")
        await r.sleepers(1)  # the ping loop

        await fake_agora.peer_leaves()
        (frame,) = await r.received("unsubscribe")
        await r.sleepers(2)  # the ping loop and the recovery debounce
        await r.advance(PEER_REJOIN_DEBOUNCE_S)
        await asyncio.wait_for(peer_left.wait_for(1), SESSION_TIMEOUT_S)
        await r.advance(PEER_REJOIN_DEBOUNCE_S * 10)

        assert frame["_message"]["stream_id"] == DEVICE_UID
        assert peer_left.calls == [DEVICE_UID]

    async def test_a_device_back_within_the_debounce_is_not_reported(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        peer_left = Recorder()
        r = new_session(on_peer_left=peer_left)
        await r.join()
        await r.received("subscribe")
        await r.sleepers(1)  # the ping loop
        await fake_agora.peer_leaves()
        await r.received("unsubscribe")
        await r.sleepers(2)  # the ping loop and the recovery debounce

        await fake_agora.announce_peer()
        await r.received("subscribe", 2)
        await r.advance(PEER_REJOIN_DEBOUNCE_S)

        assert peer_left.calls == []
