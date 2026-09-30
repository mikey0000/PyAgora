"""The shared value types: secret redaction, fingerprints, defaults."""

from __future__ import annotations

import pytest

from pyagorartc import const
from pyagorartc.models import (
    ChannelCredentials,
    ChannelEncryption,
    CloseReason,
    EdgeAddress,
    ICEServer,
    SessionOptions,
    TurnCredentialStrategy,
    TurnMode,
    as_int,
    fingerprint,
)
from tests._helpers import (
    CREDENTIALS,
    ENCRYPTION_SECRET,
    RTC_TOKEN,
    RTM_CREDENTIALS,
    RTM_TOKEN,
    TICKET,
    TURN_CREDENTIAL,
)


class TestFingerprint:
    def test_is_short_hex_and_stable(self) -> None:
        first, second = fingerprint(RTC_TOKEN), fingerprint(RTC_TOKEN)

        assert first == second
        assert len(first) == 12
        assert int(first, 16) >= 0

    def test_differs_between_secrets(self) -> None:
        assert fingerprint(RTC_TOKEN) != fingerprint(RTM_TOKEN)

    def test_is_what_a_credentials_repr_shows_for_the_token(self) -> None:
        assert fingerprint(RTC_TOKEN) in repr(CREDENTIALS)


class TestRedaction:
    def test_channel_credentials_repr_hides_the_token(self) -> None:
        text = repr(CREDENTIALS)

        assert RTC_TOKEN not in text
        assert CREDENTIALS.channel_name in text
        assert str(CREDENTIALS.uid) in text

    def test_channel_credentials_repr_hides_the_app_id_and_license(self) -> None:
        creds = ChannelCredentials(
            app_id="app-id-secret", channel_name="c", token=RTC_TOKEN, uid=1, license="license-secret"
        )

        text = repr(creds)

        assert "app-id-secret" not in text
        assert "license-secret" not in text
        assert "<present>" in text

    def test_encryption_repr_hides_secret_and_salt(self) -> None:
        enc = ChannelEncryption(mode="aes-256-gcm2", secret=ENCRYPTION_SECRET, salt=b"salty")

        text = repr(enc)

        assert ENCRYPTION_SECRET not in text
        assert "salty" not in text
        assert "aes-256-gcm2" in text

    def test_credentials_repr_hides_nested_encryption(self) -> None:
        enc = ChannelEncryption(mode="aes-256-gcm2", secret=ENCRYPTION_SECRET, salt=b"salty")
        creds = ChannelCredentials(app_id="a", channel_name="c", token=RTC_TOKEN, uid=1, encryption=enc)

        text = repr(creds)

        assert ENCRYPTION_SECRET not in text
        assert "salty" not in text
        assert "aes-256-gcm2" in text

    def test_rtm_credentials_repr_hides_the_token(self) -> None:
        text = repr(RTM_CREDENTIALS)

        assert RTM_TOKEN not in text
        assert RTM_CREDENTIALS.user_id in text

    def test_edge_address_repr_hides_credentials_and_ticket(self) -> None:
        edge = EdgeAddress(ip="203.0.113.1", port=443, username="u", credentials=TURN_CREDENTIAL, ticket=TICKET)

        text = repr(edge)

        assert TURN_CREDENTIAL not in text
        assert TICKET not in text
        assert "203.0.113.1" in text
        assert "<redacted>" in text

    def test_ice_server_repr_hides_the_credential_and_keeps_the_urls(self) -> None:
        server = ICEServer(urls=["turn:203.0.113.1:443?transport=udp"], username="u", credential=TURN_CREDENTIAL)

        text = repr(server)

        assert TURN_CREDENTIAL not in text
        assert "turn:203.0.113.1:443?transport=udp" in text
        assert "username='u'" in text
        assert "<redacted>" in text


class TestSessionOptions:
    def test_defaults_are_the_shipped_mammotion_behaviour(self) -> None:
        options = SessionOptions()

        assert options.client_codec == "vp8"
        assert options.send_set_client_role is False
        assert options.ortc_dtls_role == "server"
        assert options.strip_mid_extension is True
        assert options.declare_remote_video_ssrc is False
        assert options.disable_audio is False
        assert options.verify_ssl is True
        assert options.end_on_p2p_lost is False
        assert options.prealloc_pc is True
        assert options.subscribe_requires_online is True
        assert options.renew_debounce_s == 30.0

    def test_defaults_come_from_const(self) -> None:
        options = SessionOptions()

        assert (
            options.client_codec,
            options.ortc_dtls_role,
            options.renew_debounce_s,
            options.join_timeout_s,
            options.connect_timeout_s,
        ) == (
            const.DEFAULT_CLIENT_CODEC,
            const.DEFAULT_ORTC_DTLS_ROLE,
            const.RENEW_TOKEN_DEBOUNCE_S,
            const.JOIN_TIMEOUT_S,
            const.GATEWAY_CONNECT_TIMEOUT_S,
        )
        assert ChannelCredentials(app_id="a", channel_name="c", token="t", uid=1).area_code == const.DEFAULT_AREA_CODE

    def test_is_immutable(self) -> None:
        with pytest.raises(AttributeError):
            SessionOptions().client_codec = "h264"  # type: ignore[misc]

    def test_extra_join_attributes_are_a_read_only_copy(self) -> None:
        given = {"enableXR": False}
        options = SessionOptions(extra_join_attributes=given)
        given["enableXR"] = True

        assert options.extra_join_attributes == {"enableXR": False}
        with pytest.raises(TypeError):
            options.extra_join_attributes["enableXR"] = True  # type: ignore[index]

    def test_is_hashable_and_equal_by_value_with_extra_join_attributes(self) -> None:
        first = SessionOptions(extra_join_attributes={"enableXR": False})
        second = SessionOptions(extra_join_attributes={"enableXR": False})

        assert first == second
        assert hash(first) == hash(second)
        assert first != SessionOptions(extra_join_attributes={"enableXR": True})


class TestAsInt:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(12, 12), (-3, -3), ("12", 12), (" 12 ", 12), ("+7", 7), ("-7", -7), ("0012", 12)],
    )
    def test_reads_ints_and_integer_text(self, value: object, expected: int) -> None:
        assert as_int(value) == expected

    @pytest.mark.parametrize("value", [True, False, 1.5, "1.5", "1e3", "", "12a", "\u00b2", "1_000", None, [1]])
    def test_rejects_everything_else(self, value: object) -> None:
        assert as_int(value) is None

    def test_returns_the_default_for_a_rejected_value(self) -> None:
        assert as_int("x", -1) == -1


class TestEnums:
    def test_turn_modes_match_the_sdk_numbering(self) -> None:
        assert [m.value for m in TurnMode] == [1, 2, 3, 4]

    def test_credential_strategies(self) -> None:
        assert {s.value for s in TurnCredentialStrategy} == {"uid", "detail_first"}

    def test_close_reasons_cover_every_documented_ending(self) -> None:
        assert {r.value for r in CloseReason} == {
            "closed_by_host",
            "gateway_quit",
            "p2p_lost",
            "socket_closed",
            "deadline",
            "join_failed",
        }
