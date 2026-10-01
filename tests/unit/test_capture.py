"""The opt-in capture logger: silent by default, every exchange redacted when enabled."""

from __future__ import annotations

import json
import logging

import pytest

from pyagorartc.capture import CAPTURE_LOGGER, REDACTED, capture, redact
from tests._helpers import RTC_TOKEN, TURN_CREDENTIAL

_FRAME = {
    "_id": "abc123",
    "_type": "join_v3",
    "_message": {
        "channel_key": RTC_TOKEN,
        "app_id": "app-id-test",
        "ap_response": {"cert": "ticket-value", "detail": {"4": TURN_CREDENTIAL, "8": "user", "19": "sha-256 AA:BB"}},
        "ortc": {"iceParameters": {"iceUfrag": "uf", "icePwd": "ice-pwd-secret"}},
        "uid": 123456,
        "codec": "vp8",
    },
}


class TestRedact:
    def test_replaces_every_credential_value_and_keeps_everything_else(self) -> None:
        out = redact(_FRAME)

        message = out["_message"]  # type: ignore[index]
        assert message["channel_key"] == REDACTED
        assert message["app_id"] == REDACTED
        assert message["ap_response"]["cert"] == REDACTED
        assert message["ap_response"]["detail"]["4"] == REDACTED
        assert message["ap_response"]["detail"]["8"] == "user"
        assert message["ap_response"]["detail"]["19"] == "sha-256 AA:BB"
        assert message["ortc"]["iceParameters"]["icePwd"] == REDACTED
        assert message["ortc"]["iceParameters"]["iceUfrag"] == "uf"
        assert (message["uid"], message["codec"], out["_type"]) == (123456, "vp8", "join_v3")  # type: ignore[index]

    @pytest.mark.regression
    def test_redacts_the_token_shaped_ap_detail_10(self) -> None:
        """The capture of 2026-10-01 logged AP detail ``10``, an AccessToken2-shaped ``007e…`` value, in clear."""
        out = redact({"detail": {"10": "007e-not-a-real-token", "8": "987654"}})

        assert out == {"detail": {"10": REDACTED, "8": "987654"}}

    def test_leaves_empty_credentials_alone(self) -> None:
        assert redact({"token": "", "cert": None}) == {"token": "", "cert": None}

    def test_does_not_mutate_the_input(self) -> None:
        original = {"token": RTC_TOKEN, "nested": [{"key": "k"}]}

        redact(original)

        assert original == {"token": RTC_TOKEN, "nested": [{"key": "k"}]}

    def test_handles_lists_and_numeric_keys(self) -> None:
        assert redact([{"4": "x"}, {"detail": [{"4": "y"}]}]) == [{"4": "x"}, {"detail": [{"4": REDACTED}]}]


class TestCapture:
    def test_is_silent_unless_enabled(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=CAPTURE_LOGGER.name):
            capture("gateway", "in", json.dumps(_FRAME))

        assert not [r for r in caplog.records if r.name == CAPTURE_LOGGER.name]

    def test_logs_one_redacted_json_line_per_exchange(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger=CAPTURE_LOGGER.name):
            capture("gateway", "in", json.dumps(_FRAME))
            capture("ap", "out", {"key": RTC_TOKEN, "cname": "chan"})

        lines = [r.getMessage() for r in caplog.records if r.name == CAPTURE_LOGGER.name]
        assert len(lines) == 2
        assert lines[0].startswith("gateway in {")
        assert json.loads(lines[0][len("gateway in ") :])["_message"]["channel_key"] == REDACTED
        assert lines[1] == f'ap out {{"key":"{REDACTED}","cname":"chan"}}'
        assert RTC_TOKEN not in caplog.text
        assert TURN_CREDENTIAL not in caplog.text

    def test_logs_a_non_json_frame_as_raw_text(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger=CAPTURE_LOGGER.name):
            capture("gateway", "in", "not json")

        assert "gateway in raw=not json" in caplog.text
