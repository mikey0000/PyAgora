"""Constitution §6 over a real socket: no token, ticket or ICE password reaches a log line or a repr."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyagorartc.const import PEER_REJOIN_DEBOUNCE_S
from pyagorartc.models import SessionOptions
from tests._helpers import RENEWED_TOKEN, RTC_TOKEN, SECRET_VALUES, leaked_secrets
from tests.unit._fakes import Recorder

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyagorartc.ap import APResponse
    from tests.fakegateway import FakeAgora
    from tests.integration._helpers import SessionRig


class TestLogs:
    @pytest.mark.regression
    async def test_a_renew_token_frame_reaches_no_log_line_through_the_websockets_logger(
        self, new_session: Callable[..., SessionRig], caplog: pytest.LogCaptureFixture
    ) -> None:
        """``WebsocketsTransport`` let websockets log each frame at DEBUG; a renew_token frame fits whole, token too."""
        r = new_session()
        await r.join()

        await r.session.renew_token()
        await r.received("renew_token")

        assert not [record for record in caplog.records if RTC_TOKEN in record.getMessage()]

    async def test_a_whole_session_logs_no_secret_at_debug(
        self, fake_agora: FakeAgora, new_session: Callable[..., SessionRig], caplog: pytest.LogCaptureFixture
    ) -> None:
        r = new_session(
            SessionOptions(send_set_client_role=True),
            token_provider=Recorder(returns=[RENEWED_TOKEN]),
            on_peer_left=Recorder(),
        )
        await r.join()
        await r.received("subscribe")
        await r.sleepers(1)
        await fake_agora.send_token_will_expire()
        await r.received("renew_token")
        await fake_agora.peer_leaves()
        await r.received("unsubscribe")
        await r.sleepers(2)
        await r.advance(PEER_REJOIN_DEBOUNCE_S)

        await r.session.close()

        assert caplog.records
        assert leaked_secrets(caplog.records) == []


class TestRepr:
    async def test_the_session_and_its_ap_response_show_no_secret(
        self, ap: APResponse, new_session: Callable[..., SessionRig]
    ) -> None:
        r = new_session()
        await r.join()

        texts = f"{r.session!r} {ap!r} {ap.get_gateway_addresses()!r}"

        assert [value for value in SECRET_VALUES if value in texts] == []
