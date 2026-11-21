"""Encryption of secrets at rest (channel credentials) with Fernet.

`keys` is SIM_SECRET_KEY: one Fernet key or a comma-separated list. The FIRST key encrypts;
every key is tried on decrypt (MultiFernet), so to rotate, put the new key first, re-save the
secrets, then drop the old key. v1 used the same scheme and key, so a v1 token decrypts here."""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoError(ValueError):
    """No usable key, a wrong key, or a token that does not hold a JSON object."""


def _fernet(keys: str) -> MultiFernet:
    parts = [key.strip() for key in keys.split(",") if key.strip()]
    if not parts:
        raise CryptoError("SIM_SECRET_KEY is empty: set a Fernet key (or a comma-separated list)")
    try:
        return MultiFernet([Fernet(key.encode()) for key in parts])
    except ValueError as exc:
        raise CryptoError("SIM_SECRET_KEY holds an invalid Fernet key") from exc


def encrypt_json(data: dict[str, Any], keys: str) -> str:
    """A Fernet token of the JSON object, made with the first key."""
    return _fernet(keys).encrypt(json.dumps(data, sort_keys=True).encode()).decode()


def decrypt_json(token: str, keys: str) -> dict[str, Any]:
    """The JSON object inside a token made with any of the keys."""
    try:
        plain = _fernet(keys).decrypt(token.encode())
    except InvalidToken:
        raise CryptoError("cannot decrypt: wrong key or the key was rotated out") from None
    try:
        data = json.loads(plain)
    except ValueError:
        raise CryptoError("decrypted secret is not JSON") from None
    if not isinstance(data, dict):
        raise CryptoError("decrypted secret is not a JSON object")
    return data
