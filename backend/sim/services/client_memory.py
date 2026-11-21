"""`client.memory`: durable facts about a person, `[{"fact", "event_id", "at"}]`
(docs/context.md §4). Facts are compared by normalized text, so the same fact is never stored
twice. Pure functions; callers assign the returned list (JSONB change tracking)."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

_TRIM = " \t\n.,;:!?…\"'«»()-—"


def normalize_fact(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split()).strip(_TRIM)


def _item(fact: str, event_id: int | None, at: datetime) -> dict[str, Any]:
    return {"fact": fact, "event_id": event_id, "at": at.isoformat()}


def add_facts(memory: Iterable[dict[str, Any]], facts: Iterable[str], *,
              event_id: int | None, at: datetime) -> list[dict[str, Any]]:
    """Old memory plus the facts not already in it (by normalized text)."""
    out = [dict(item) for item in memory]
    seen = {normalize_fact(str(item.get("fact", ""))) for item in out}
    for fact in facts:
        clean = " ".join(fact.split())
        key = normalize_fact(clean)
        if key and key not in seen:
            out.append(_item(clean, event_id, at))
            seen.add(key)
    return out


def replace_facts(memory: Iterable[dict[str, Any]], facts: Iterable[str], *,
                  event_id: int | None, at: datetime) -> list[dict[str, Any]]:
    """The memory step's cleaned list: kept facts keep their original event and time."""
    old = {normalize_fact(str(item.get("fact", ""))): item for item in memory}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in facts:
        clean = " ".join(fact.split())
        key = normalize_fact(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        previous = old.get(key)
        if previous is not None:
            out.append({**previous, "fact": clean})
        else:
            out.append(_item(clean, event_id, at))
    return out
