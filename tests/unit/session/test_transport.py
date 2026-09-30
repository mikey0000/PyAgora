from __future__ import annotations

import ssl

import pytest

from pyagorartc.session.transport import ssl_for


class TestSslFor:
    def test_verifies_certificates_and_hostnames_by_default(self) -> None:
        context = ssl_for("wss://203-0-113-10.edge.example:4713", verify_ssl=True)

        assert context is not None
        assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, True)

    def test_skips_verification_only_when_asked(self) -> None:
        context = ssl_for("wss://203-0-113-10.edge.example:4713", verify_ssl=False)

        assert context is not None
        assert (context.verify_mode, context.check_hostname) == (ssl.CERT_NONE, False)

    def test_passes_no_context_for_a_plain_ws_url(self) -> None:
        assert ssl_for("ws://127.0.0.1:8765", verify_ssl=True) is None

    @pytest.mark.regression
    def test_passes_no_context_for_a_ws_scheme_in_any_case(self) -> None:
        """``startswith("ws://")`` gave ``WS://`` a verified TLS context; URL schemes are case-insensitive."""
        assert ssl_for("WS://127.0.0.1:8765", verify_ssl=True) is None
        assert ssl_for("Ws://127.0.0.1:8765", verify_ssl=True) is None
