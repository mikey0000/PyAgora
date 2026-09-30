"""``AgoraSession.join`` against the fake Agora: the join frame it sends, the answer it builds, how a join fails."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyagorartc.const import EDGE_DOMAIN_SUFFIX
from pyagorartc.exceptions import JoinRejectedError, JoinTimeoutError
from pyagorartc.models import CloseReason, SessionOptions
from pyagorartc.sdp import extract_inline_candidates
from tests._helpers import RTC_TOKEN
from tests.fakegateway._common import SERVER_ICE_UFRAG, dtls_fingerprint
from tests.integration._helpers import CHROME_OFFER, GO2RTC_OFFER, SESSION_ID, SESSION_TIMEOUT_S

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import SessionRig

MID_EXTENSION = "urn:ietf:params:rtp-hdrext:sdes:mid"
GATEWAY_FINGERPRINT = f"a=fingerprint:sha-256 {dtls_fingerprint('fake-gateway-dtls')}"


class TestJoinFrame:
    async def test_dials_the_gateway_edge_the_ap_named_as_a_wss_edge_url(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()

        await r.join()

        dashed = fake_agora.state.gateway_host.replace(".", "-")
        assert r.transport.urls == [f"wss://{dashed}{EDGE_DOMAIN_SUFFIX}:{fake_agora.state.gateway_port}"]
        assert r.transport.verify_ssl == [True]

    async def test_nests_the_user_attributes_and_carries_the_join_token(
        self, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()

        await r.join()

        (join,) = await r.received("join_v3")
        assert list(join["_message"]["attributes"]) == ["userAttributes"]
        assert join["_message"]["channel_key"] == RTC_TOKEN

    async def test_carries_the_offers_inline_candidates_in_the_ortc(
        self, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()

        await r.join()

        (join,) = await r.received("join_v3")
        sent = [c["ip"] for c in join["_message"]["ortc"]["iceParameters"]["candidates"]]
        assert sent == [c.candidate.split()[4] for c in extract_inline_candidates(CHROME_OFFER)]

    async def test_sends_no_set_client_role_by_default(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        await r.received("subscribe")

        assert fake_agora.state.log.received_of_type("set_client_role") == []

    async def test_sends_set_client_role_when_asked(self, new_session: Callable[..., SessionRig]) -> None:
        r = new_session(SessionOptions(send_set_client_role=True))

        await r.join()

        (frame,) = await r.received("set_client_role")
        assert frame["_message"]["role"] == "host"


class TestAnswer:
    async def test_mirrors_the_fakes_default_server_role_as_passive(
        self, new_session: Callable[..., SessionRig]
    ) -> None:
        answer = await new_session().join()

        assert "a=setup:passive" in answer

    async def test_answers_active_when_the_gateway_takes_the_client_role(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(dtls_role="client")

        answer = await new_session().join()

        assert "a=setup:active" in answer

    async def test_carries_the_gateway_ice_credentials_and_fingerprint_and_no_mid_extension(
        self, new_session: Callable[..., SessionRig]
    ) -> None:
        answer = await new_session().join()

        assert f"a=ice-ufrag:{SERVER_ICE_UFRAG}" in answer
        assert GATEWAY_FINGERPRINT in answer
        assert MID_EXTENSION not in answer

    async def test_negotiates_a_go2rtc_offer_video_first(self, new_session: Callable[..., SessionRig]) -> None:
        answer = await new_session().join(GO2RTC_OFFER)

        media = [line.split()[0] for line in answer.splitlines() if line.startswith("m=")]
        assert media == ["m=video", "m=audio"]
        assert "a=setup:passive" in answer

    async def test_declares_the_devices_video_ssrc_when_asked(self, new_session: Callable[..., SessionRig]) -> None:
        r = new_session(SessionOptions(declare_remote_video_ssrc=True))

        answer = await r.join()

        assert "a=ssrc:44444444 cname:fake-device-cname" in answer
        assert "a=msid:agora agora-video" in answer

    async def test_declares_no_ssrc_by_default(self, new_session: Callable[..., SessionRig]) -> None:
        answer = await new_session().join()

        assert "a=ssrc:" not in answer
        assert "a=msid:" not in answer


class TestJoinFailure:
    async def test_a_rejected_join_raises_with_the_gateway_code_and_reports_join_failed_once(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(reject_join={"code": 2013, "message": "ERR_INVALID_VENDOR_KEY"})
        r = new_session()

        with pytest.raises(JoinRejectedError) as caught:
            await r.join()

        assert caught.value.code == 2013
        assert r.closed.calls == [CloseReason.JOIN_FAILED]

    async def test_a_rejected_join_sends_no_leave_and_leaves_nothing_running(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(reject_join={"code": 110, "message": "ERR_NO_AUTHORIZED"})
        r = new_session()
        with pytest.raises(JoinRejectedError):
            await r.join()

        await asyncio.wait_for(fake_agora.gateway.connections[0].ws.wait_closed(), SESSION_TIMEOUT_S)

        assert fake_agora.state.log.received_of_type("leave") == []
        assert all(task.done() for task in r.spawned)

    async def test_a_join_result_later_than_the_timeout_raises_join_timeout(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig]
    ) -> None:
        fake_agora.control(join_delay_s=20.0)
        r = new_session(SessionOptions(join_timeout_s=15.0))
        join = asyncio.ensure_future(r.session.join(CHROME_OFFER, SESSION_ID))
        await r.sleepers(2)

        await r.advance(15.0)

        with pytest.raises(JoinTimeoutError):
            await asyncio.wait_for(join, SESSION_TIMEOUT_S)
        assert r.closed.calls == [CloseReason.JOIN_FAILED]
