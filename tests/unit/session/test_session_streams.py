from __future__ import annotations

import asyncio
import logging

import pytest

from pyagorartc.const import (
    DECLARED_SSRC_TIMEOUT_S,
)
from pyagorartc.models import RemoteStream, SessionOptions
from tests._helpers import UID, load_json_fixture
from tests.unit.session._helpers import (
    ASSIGNED_UID,
    GO2RTC_OFFER,
    MARKER_UID,
    OFFER,
    OTHER_PUBLISHER,
    PUBLISHER,
    SESSION_ID,
    STREAM_SSRC,
    TIMEOUT,
    Recorder,
    announced,
    event,
    join_ok_with_uid,
    reply,
    rig,
    subscribe_ack,
)


class TestExistingStreams:
    async def test_subscribes_once_to_a_stream_listed_in_the_join_payload(self) -> None:
        r = rig()

        await r.join("join_ok_with_streams", extra=(load_json_fixture("gateway/on_add_video_stream.json"),))
        await r.mark()

        (frame,) = r.subscribes()
        assert (frame["_message"]["ssrcId"], frame["_message"]["codec"], frame["_message"]["rtx"]) == (
            STREAM_SSRC,
            "vp8",
            False,
        )

    async def test_counts_the_publisher_of_an_existing_stream_as_present(self) -> None:
        r = rig()

        await r.join("join_ok_with_streams")

        assert r.session.remote_users == frozenset({PUBLISHER})
        assert [s.ssrc for s in r.session.remote_streams] == [STREAM_SSRC]

    async def test_ignores_an_existing_stream_from_another_uid_than_the_target(self) -> None:
        r = rig(SessionOptions(target_uid=OTHER_PUBLISHER))

        await r.join("join_ok_with_streams")
        await r.mark(OTHER_PUBLISHER)

        assert r.subscribes(PUBLISHER) == []


class TestStreamAnnouncements:
    async def test_subscribes_with_the_options_codec_and_the_joins_rtx(self) -> None:
        r = rig(SessionOptions(client_codec="h264"))
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))

        (frame,) = await r.sent_type("subscribe")
        assert (frame["_message"]["stream_id"], frame["_message"]["codec"], frame["_message"]["rtx"]) == (
            PUBLISHER,
            "h264",
            True,
        )

    async def test_waits_for_the_publisher_when_the_stream_arrives_first(self) -> None:
        r = rig()
        await r.join("join_ok_rtx")
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.mark()
        assert r.subscribes() == []

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        await r.sent_type("subscribe", 2)

        assert len(r.subscribes()) == 1

    async def test_logs_the_held_publisher_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        r = rig()
        await r.join("join_ok_rtx")

        with caplog.at_level(logging.DEBUG, logger="pyagorartc.session.session"):
            r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
            await r.mark()

        # A host diagnoses Q18 from this line: the stream is known, its publisher's presence is not.
        (held,) = [
            rec
            for rec in caplog.records
            if rec.name == "pyagorartc.session.session" and "on_user_online" in rec.getMessage()
        ]
        assert held.levelno == logging.DEBUG
        assert f"uid {PUBLISHER}" in held.getMessage()

    async def test_ignores_streams_from_uids_other_than_the_target(self) -> None:
        r = rig(SessionOptions(target_uid=OTHER_PUBLISHER))
        await r.join("join_ok_rtx")

        r.conn.feed(event("on_user_online", uid=PUBLISHER))
        r.conn.feed(event("on_add_video_stream", uid=PUBLISHER))
        await r.mark(OTHER_PUBLISHER)

        assert r.subscribes(PUBLISHER) == []
        assert r.session.remote_users == frozenset({OTHER_PUBLISHER})

    async def test_subscribes_once_to_a_repeated_announcement(self) -> None:
        r = rig()
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.mark()

        assert len(r.subscribes()) == 1

    async def test_tells_the_host_about_each_subscribed_stream(self) -> None:
        on_stream = Recorder()
        r = rig(on_stream=on_stream)
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream_minimal.json"))
        await asyncio.wait_for(on_stream.wait_for(1), TIMEOUT)

        assert on_stream.calls == [RemoteStream(uid=PUBLISHER, ssrc=STREAM_SSRC)]

    async def test_ignores_an_announcement_without_an_ssrc(self) -> None:
        r = rig()
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream_no_ssrc.json"))
        await r.mark()

        assert r.subscribes() == []
        assert r.session.is_joined


class TestSubscribeWithoutPresence:
    """``subscribe_requires_online=False``: the announcement alone is enough (D28, PetKit)."""

    async def test_subscribes_to_a_stream_whose_publisher_never_came_online(self) -> None:
        r = rig(SessionOptions(subscribe_requires_online=False))
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))

        (frame,) = await r.sent_type("subscribe")
        assert (frame["_message"]["stream_id"], frame["_message"]["ssrcId"]) == (PUBLISHER, STREAM_SSRC)

    async def test_counts_the_announced_publisher_as_present(self) -> None:
        r = rig(SessionOptions(subscribe_requires_online=False))
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.sent_type("subscribe")

        assert PUBLISHER in r.session.remote_users

    async def test_a_later_on_user_online_does_not_subscribe_again(self) -> None:
        r = rig(SessionOptions(subscribe_requires_online=False))
        await r.join("join_ok_rtx")
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.sent_type("subscribe")

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        await r.mark()

        assert len(r.subscribes()) == 1

    async def test_unsubscribes_and_forgets_the_publisher_when_it_goes_offline(self) -> None:
        r = rig(SessionOptions(subscribe_requires_online=False))
        await r.join("join_ok_rtx")
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.sent_type("subscribe")

        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))

        (frame,) = await r.sent_type("unsubscribe")
        assert frame["_message"]["stream_id"] == PUBLISHER
        assert PUBLISHER not in r.session.remote_users
        assert r.session.remote_streams == ()

    async def test_still_ignores_a_stream_from_another_uid_than_the_target(self) -> None:
        r = rig(SessionOptions(subscribe_requires_online=False, target_uid=OTHER_PUBLISHER))
        await r.join("join_ok_rtx")

        r.conn.feed(event("on_add_video_stream", uid=PUBLISHER))
        await r.mark(OTHER_PUBLISHER)

        assert r.subscribes(PUBLISHER) == []
        assert PUBLISHER not in r.session.remote_users


class TestSubscribeRetry:
    async def test_resubscribes_after_a_refused_ack_until_one_succeeds(self) -> None:
        r = rig(SessionOptions(subscribe_retry_attempts=3, subscribe_retry_delay_s=1.0))
        await r.join(extra=announced())
        (first,) = await r.sent_type("subscribe")
        r.conn.feed(subscribe_ack(first, ok=False))
        await r.sleepers(2)

        r.sleep.advance(1.0)
        second = (await r.sent_type("subscribe", 2))[1]
        r.conn.feed(subscribe_ack(second, ok=True))
        await r.sleepers(2)
        r.sleep.advance(1.0)
        await r.mark()

        assert len(r.subscribes()) == 2

    async def test_gives_up_after_the_configured_attempts_without_an_ack(self) -> None:
        r = rig(SessionOptions(subscribe_retry_attempts=1, subscribe_retry_delay_s=1.0))
        await r.join(extra=announced())
        await r.sent_type("subscribe")
        await r.sleepers(2)

        r.sleep.advance(1.0)
        await r.sent_type("subscribe", 2)
        await r.mark()

        r.sleep.advance(1.0)
        await r.mark(MARKER_UID + 1)

        assert len(r.subscribes()) == 2

    @pytest.mark.regression
    async def test_stops_retrying_once_the_publisher_goes_offline(self) -> None:
        """The retry task outlived its publisher and kept re-subscribing to a stream that was gone."""
        r = rig(SessionOptions(subscribe_retry_attempts=3, subscribe_retry_delay_s=1.0))
        await r.join(extra=announced())
        await r.sent_type("subscribe")
        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))
        await r.sent_type("unsubscribe")

        r.sleep.advance(1.0)
        await r.mark()

        assert len(r.subscribes()) == 1

    @pytest.mark.regression
    async def test_a_returning_publisher_gets_one_retry_loop(self) -> None:
        """A publisher back with the same ssrc got a second retry loop beside the first, doubling every re-send."""
        r = rig(SessionOptions(subscribe_retry_attempts=3, subscribe_retry_delay_s=1.0))
        await r.join(extra=announced())
        await r.sent_type("subscribe")
        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))
        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))
        await r.sent_type("subscribe", 2)

        r.sleep.advance(1.0)
        await r.mark()

        assert len(r.subscribes()) == 3

    async def test_subscribes_once_when_retries_are_off_even_if_refused(self) -> None:
        r = rig()
        await r.join(extra=announced())
        (first,) = await r.sent_type("subscribe")

        r.conn.feed(subscribe_ack(first, ok=False))
        await r.mark()

        assert len(r.subscribes()) == 1


class TestUserPresence:
    async def test_tracks_publishers_coming_and_going(self) -> None:
        r = rig()
        await r.join("join_ok_rtx")

        r.conn.feed(load_json_fixture("gateway/on_user_online.json"))
        await r.mark()
        assert PUBLISHER in r.session.remote_users
        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))
        await r.mark(MARKER_UID + 1)

        assert PUBLISHER not in r.session.remote_users

    async def test_unsubscribes_a_departed_publishers_stream(self) -> None:
        r = rig()
        await r.join(extra=announced())
        await r.sent_type("subscribe")

        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))

        (frame,) = await r.sent_type("unsubscribe")
        assert frame["_message"]["stream_id"] == PUBLISHER
        assert r.session.remote_streams == ()

    @pytest.mark.regression
    async def test_unsubscribes_once_from_a_publisher_with_two_streams(self) -> None:
        """``unsubscribe`` names a uid, not a stream, yet the session sent one per subscribed ssrc."""
        r = rig()
        await r.join(extra=announced())
        r.conn.feed(event("on_add_video_stream", ssrcId=STREAM_SSRC + 1))
        await r.sent_type("subscribe", 2)

        r.conn.feed(load_json_fixture("gateway/on_user_offline.json"))
        await r.mark()

        assert len(r.conn.sent_of_type("unsubscribe")) == 1

    async def test_ignores_presence_events_for_our_own_uid(self) -> None:
        r = rig()
        await r.join("join_ok_rtx")

        r.conn.feed(event("on_user_online", uid=UID))
        await r.mark()

        assert UID not in r.session.remote_users

    async def test_ignores_presence_events_without_a_uid(self) -> None:
        r = rig()
        await r.join(extra=announced())

        r.conn.feed(load_json_fixture("gateway/on_user_offline_no_uid.json"))
        await r.mark()

        assert PUBLISHER in r.session.remote_users


class TestOwnUid:
    @pytest.mark.regression
    async def test_ignores_a_stream_announced_for_our_own_uid(self) -> None:
        """Our own uid's ``on_add_video_stream`` was kept as a remote stream (and could be declared in the answer)."""
        r = rig()
        await r.join()

        r.conn.feed(event("on_add_video_stream", uid=UID, ssrcId=STREAM_SSRC + 1))
        await r.mark()

        assert [s.uid for s in r.session.remote_streams if s.uid == UID] == []
        assert r.subscribes(UID) == []

    @pytest.mark.regression
    async def test_forgets_our_gateway_uid_seen_before_the_join_result(self) -> None:
        """Presence for the uid the gateway assigned us, seen before the result named it, stayed a remote user."""
        r = rig()
        joining = asyncio.ensure_future(r.session.join(OFFER, SESSION_ID))
        await r.sent(1)
        r.conn.feed(event("on_user_online", uid=ASSIGNED_UID))
        r.conn.feed(event("on_add_video_stream", uid=ASSIGNED_UID))

        r.conn.feed({**join_ok_with_uid(ASSIGNED_UID), "_id": r.conn.sent[0]["_id"]})
        await asyncio.wait_for(joining, TIMEOUT)
        await r.mark()

        assert ASSIGNED_UID not in r.session.remote_users
        assert ASSIGNED_UID not in {s.uid for s in r.session.remote_streams}
        assert r.subscribes(ASSIGNED_UID) == []


class TestDeclaredRemoteSsrc:
    async def test_declares_an_existing_stream_without_waiting(self) -> None:
        r = rig(SessionOptions(declare_remote_video_ssrc=True))

        answer = await r.join("join_ok_with_streams", offer=GO2RTC_OFFER)

        assert f"a=ssrc:{STREAM_SSRC} cname:" in answer

    async def test_holds_the_answer_until_a_stream_is_announced(self) -> None:
        r = rig(SessionOptions(declare_remote_video_ssrc=True))
        task = asyncio.ensure_future(r.session.join(GO2RTC_OFFER, SESSION_ID))
        await r.sent(1)
        r.conn.feed(reply("join_ok_rtx", r.conn.sent[0]))
        await r.sleepers(2)
        assert not task.done()

        r.conn.feed(load_json_fixture("gateway/on_add_video_stream.json"))

        answer = await asyncio.wait_for(task, TIMEOUT)
        assert f"a=ssrc:{STREAM_SSRC} cname:" in answer

    async def test_answers_without_a_remote_ssrc_after_the_timeout(self) -> None:
        r = rig(SessionOptions(declare_remote_video_ssrc=True))
        task = asyncio.ensure_future(r.session.join(GO2RTC_OFFER, SESSION_ID))
        await r.sent(1)
        r.conn.feed(reply("join_ok_rtx", r.conn.sent[0]))
        await r.sleepers(2)

        r.sleep.advance(DECLARED_SSRC_TIMEOUT_S)

        answer = await asyncio.wait_for(task, TIMEOUT)
        assert "a=ssrc:" not in answer
