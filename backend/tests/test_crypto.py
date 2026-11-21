"""Fernet secrets at rest: round trip, key rotation, wrong keys, v1 tokens."""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from sim.crypto import CryptoError, decrypt_json, encrypt_json

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()
CREDS = {"api_id": 12345, "api_hash": "abc", "session": "1BVts...", "proxy": ""}


def test_round_trip_and_ciphertext_hides_the_data():
    token = encrypt_json(CREDS, OLD)
    assert isinstance(token, str)
    assert "abc" not in token and "1BVts" not in token
    assert decrypt_json(token, OLD) == CREDS


def test_rotation_first_key_encrypts_all_keys_decrypt():
    old_token = encrypt_json(CREDS, OLD)
    rotated = f"{NEW}, {OLD}"
    assert decrypt_json(old_token, rotated) == CREDS
    new_token = encrypt_json(CREDS, rotated)
    assert decrypt_json(new_token, NEW) == CREDS          # made with the first key
    with pytest.raises(CryptoError):
        decrypt_json(new_token, OLD)


def test_wrong_key_is_a_crypto_error():
    with pytest.raises(CryptoError, match="wrong key"):
        decrypt_json(encrypt_json(CREDS, OLD), NEW)


@pytest.mark.parametrize("keys", ["", " , ", "not-a-fernet-key"])
def test_missing_or_invalid_key(keys):
    with pytest.raises(CryptoError):
        encrypt_json(CREDS, keys)


def test_token_must_hold_a_json_object():
    as_list = Fernet(OLD.encode()).encrypt(b"[1, 2]").decode()
    with pytest.raises(CryptoError, match="JSON object"):
        decrypt_json(as_list, OLD)
    not_json = Fernet(OLD.encode()).encrypt(b"plain").decode()
    with pytest.raises(CryptoError, match="not JSON"):
        decrypt_json(not_json, OLD)


def test_v1_token_decrypts():
    """v1 (sim.adapters.crypto.SecretBox) stored json.dumps(sort_keys=True) under the same key."""
    v1 = {"api_id": 1, "api_hash": "h", "string_session": "s", "account_label": "@marina"}
    token = Fernet(OLD.encode()).encrypt(json.dumps(v1, sort_keys=True).encode()).decode()
    assert decrypt_json(token, OLD) == v1
