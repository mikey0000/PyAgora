from __future__ import annotations

import hashlib

from pyagorartc.ap.password import derive_password


class TestDerivePassword:
    def test_is_the_sha256_hex_of_the_decimal_uid(self) -> None:
        assert derive_password(12345678) == hashlib.sha256(b"12345678").hexdigest()

    def test_a_string_uid_hashes_the_same_as_its_int(self) -> None:
        assert derive_password("12345678") == derive_password(12345678)

    def test_different_uids_give_different_passwords(self) -> None:
        assert derive_password(1) != derive_password(2)

    def test_is_64_lowercase_hex_characters(self) -> None:
        password = derive_password(1)

        assert len(password) == 64
        assert set(password) <= set("0123456789abcdef")
