"""The uid-derived TURN password Agora's edges accept (D12)."""

from __future__ import annotations

import hashlib


def derive_password(uid: int | str) -> str:
    """The TURN credential for ``uid``: the lowercase hex SHA-256 of its decimal string.

    This is the Web SDK's ``ENCRYPT_PROXY_USERNAME_AND_PSW`` rule (``agoraRTC_N-4.24.3.js:43740-43755``),
    which shipped in both hosts; the matching username is ``str(uid)``.
    """
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()
