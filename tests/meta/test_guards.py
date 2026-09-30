"""The global safety nets in ``tests/conftest.py`` actually trip."""

from __future__ import annotations

import logging

from tests._helpers import RTC_TOKEN, RTM_TOKEN, leaked_secrets


def _record(msg: str, *args: object, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, exc_info)


class TestLeakedSecrets:
    def test_finds_a_secret_in_a_formatted_message(self) -> None:
        assert leaked_secrets([_record("token %s", RTC_TOKEN)]) == [RTC_TOKEN]

    def test_finds_a_secret_in_exception_text(self) -> None:
        assert leaked_secrets([_record("failed", exc=RuntimeError(RTM_TOKEN))]) == [RTM_TOKEN]

    def test_clean_records_report_nothing(self) -> None:
        assert leaked_secrets([_record("token fp=%s", "abc123"), _record("plain")]) == []
