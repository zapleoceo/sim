"""Comparing short facts by meaning-preserving normalization: case, «ё», spaces and punctuation
at the edges do not make a fact new. Used by the canon (sim/services/life/canon.py)."""
from __future__ import annotations

_TRIM = " \t\n.,;:!?…\"'«»()-—"


def normalize_fact(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split()).strip(_TRIM)
