"""Helpers shared by connectors."""
from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping


def header_value(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup ('' when absent)."""
    low = name.lower()
    for k, v in headers.items():
        if str(k).lower() == low:
            return str(v)
    return ""


def secret_matches(provided: str, expected: object) -> bool:
    """Constant-time compare; an empty/absent expected secret never matches."""
    if not isinstance(expected, str) or not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def new_secret() -> str:
    return secrets.token_urlsafe(32)
