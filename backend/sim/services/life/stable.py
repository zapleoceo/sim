"""Stable randomness: a number that depends only on a key, so the same event always gets the
same background noise of its voice notes (docs/persona-life.md). When she answers is decided
by her attention (sim/services/attention, docs/attention.md), not here."""
from __future__ import annotations

import hashlib


def stable_fraction(key: str) -> float:
    """A number in [0, 1) that depends only on `key`."""
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def stable_seed(key: str, modulo: int = 1_000_000) -> int:
    return int(stable_fraction(key) * modulo) + 1
