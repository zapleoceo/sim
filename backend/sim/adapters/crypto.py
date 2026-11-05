"""Fernet encryption for channel-session secrets at rest. Key(s) from env only.

SIM_SECRET_KEY may hold ONE key or a comma-separated list to support rotation: the FIRST key
encrypts new data; ALL keys are tried on decrypt (MultiFernet), so a secret encrypted under
an old key still decrypts after a new key is prepended."""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBox:
    def __init__(self, keys: str) -> None:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        if not parts:
            raise RuntimeError("SIM_SECRET_KEY must be set (Fernet key, or comma-list)")
        self.fernet = MultiFernet([Fernet(k.encode()) for k in parts])

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self.fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("cannot decrypt secret: wrong or rotated-out key") from exc

    def encrypt_json(self, data: dict[str, Any]) -> str:
        return self.encrypt(json.dumps(data, sort_keys=True))

    def decrypt_json(self, token: str | None) -> dict[str, Any]:
        if not token:
            return {}
        loaded = json.loads(self.decrypt(token))
        return loaded if isinstance(loaded, dict) else {}
