"""The day memory of a chat (docs/memory.md): what the chronicler («Летописец») returns, and the
«заметки дня» kept in `chat.day_notes`.

`chat.day_notes` is a small JSONB map from a persona-local day to that day's working memory:
`{"2026-11-22": {"notes": ["Есть дочь Аня"], "morning": "Утром ...", "morning_until": 812}}`.
- `notes`: durable facts the reply plan marked to `remember`, added by code without a model
  call and shown in the context at once; the night summary and the passport read them.
- `morning` / `morning_until`: when one day no longer fits the context budget, the oldest part
  of it (events up to `morning_until`, inclusive) is compressed into one short paragraph.
Only the last `KEEP_DAYS` days are kept. Pure functions: callers assign the returned map."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sim.domain.fact_text import normalize_fact

KEEP_DAYS = 3                   # today and the two days before it
MAX_NOTES = 60                  # notes of one day
MAX_FACTS = 40                  # facts of one day / week summary
FACT_CHARS = 300


class DiaryTask(StrEnum):
    """What the chronicler is asked to write (the `task` key of its input)."""

    DAY = "day"                 # the night summary of one day
    WEEK = "week"               # the rollup of a week's day summaries
    MORNING = "morning"         # today's oldest part, when the day does not fit the budget


class Digest(BaseModel):
    """The chronicler's answer: the summary and the durable facts of that period."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=30_000)
    facts: list[str] = Field(default_factory=list, max_length=MAX_FACTS)


def clean_facts(facts: Iterable[Any], limit: int = MAX_FACTS) -> list[str]:
    """One line each, trimmed to `FACT_CHARS`, without empty ones and repeats (by meaning)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in facts:
        fact = " ".join(str(raw or "").split())[:FACT_CHARS]
        key = normalize_fact(fact)
        if key and key not in seen:
            seen.add(key)
            out.append(fact)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class DayNotes:
    notes: list[str] = field(default_factory=list)
    morning: str = ""
    morning_until: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {"notes": list(self.notes), "morning": self.morning,
                "morning_until": self.morning_until}


def notes_of(raw: Mapping[str, Any] | None, day: date) -> DayNotes:
    """The working memory of `day`; lenient: broken values read as empty."""
    entry = (raw or {}).get(day.isoformat())
    if not isinstance(entry, Mapping):
        return DayNotes()
    notes = entry.get("notes")
    until = entry.get("morning_until")
    morning = entry.get("morning")
    return DayNotes(
        notes=clean_facts(notes if isinstance(notes, list) else [], MAX_NOTES),
        morning=morning.strip() if isinstance(morning, str) else "",
        morning_until=until if isinstance(until, int) and not isinstance(until, bool) else None,
    )


def _store(raw: Mapping[str, Any] | None, day: date, notes: DayNotes) -> dict[str, Any]:
    oldest = day - timedelta(days=KEEP_DAYS - 1)
    kept: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        found = _day(key)
        if found is not None and found >= oldest:
            kept[key] = value
    kept[day.isoformat()] = notes.as_json()
    return dict(sorted(kept.items()))


def _day(key: str) -> date | None:
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


def with_notes(raw: Mapping[str, Any] | None, day: date, facts: Iterable[str]) -> dict[str, Any]:
    """`day_notes` with `facts` appended to that day's notes (no repeats; old days pruned)."""
    current = notes_of(raw, day)
    merged = clean_facts([*current.notes, *facts], MAX_NOTES)
    return _store(raw, day, DayNotes(notes=merged, morning=current.morning,
                                     morning_until=current.morning_until))


def with_morning(raw: Mapping[str, Any] | None, day: date, morning: str,
                 until: int) -> dict[str, Any]:
    """`day_notes` with that day's compressed morning moved forward to event `until`."""
    current = notes_of(raw, day)
    return _store(raw, day, DayNotes(notes=current.notes, morning=morning.strip(),
                                     morning_until=until))
