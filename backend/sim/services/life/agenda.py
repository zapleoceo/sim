"""What fills one local day of the persona, computed by code (docs/persona-life.md, «День»).

Layers, first wins and later ones only fill the gaps it leaves:
1. the planned events of this day (`persona_day`),
2. the tail of yesterday's planned event that ran past midnight,
3. the weekly routine of this day (on a planned day only its slots past midnight: sleep),
4. the tail of yesterday's routine past midnight (usually sleep).
So a planned day covers the waking hours and the routine adds the night; without a plan the
routine alone answers «where is she now». Pure: the same input gives the same agenda."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from sim.domain.life import (
    DAY_MINUTES,
    Ambience,
    BusyLevel,
    DayEvent,
    PersonaLife,
    RoutineSlot,
    span,
)

MIN_PIECE_MIN = 10          # shorter leftovers of a lower layer are dropped


@dataclass(frozen=True)
class Slot:
    """One stretch of the day. `start`/`end` are minutes from this day's midnight; `end` may
    pass 24:00. `key` stays the same for the whole event, also for its tail after midnight."""

    start: int
    end: int
    title: str
    place: str = ""
    with_whom: tuple[str, ...] = ()
    busy: BusyLevel = BusyLevel.FREE
    details: str = ""
    outfit: str = ""
    ambience: Ambience | None = None
    planned: bool = False
    key: str = ""

    def covers(self, minute: int) -> bool:
        return self.start <= minute < self.end


@dataclass(frozen=True)
class Agenda:
    day: date
    slots: tuple[Slot, ...]
    planned: bool                   # this day has a plan (not only the routine)

    def at(self, minute: int) -> Slot | None:
        return next((slot for slot in self.slots if slot.covers(minute)), None)

    def minute_of(self, local: datetime) -> int:
        """Minutes since this agenda's midnight (a time on the next day is past 1440)."""
        return int((local.replace(tzinfo=None)
                    - datetime.combine(self.day, datetime.min.time())).total_seconds() // 60)

    def done(self, minute: int) -> list[Slot]:
        return [slot for slot in self.slots if slot.end <= minute]

    def later(self, minute: int) -> list[Slot]:
        return [slot for slot in self.slots if slot.start > minute]


def event_slot(day: date, event: DayEvent) -> Slot:
    first, last = span(event.start, event.end)
    return Slot(start=first, end=last, title=event.title, place=event.place,
                with_whom=tuple(event.with_whom), busy=event.busy, details=event.details,
                outfit=event.outfit, ambience=event.ambience, planned=True,
                key=f"{day.isoformat()}T{event.start}")


def routine_slots(life: PersonaLife, day: date) -> list[Slot]:
    return [_routine_slot(day, item) for item in life.routine if day.weekday() in item.days]


def _routine_slot(day: date, item: RoutineSlot) -> Slot:
    first, last = span(item.start, item.end)
    return Slot(start=first, end=last, title=item.activity, place=item.place,
                with_whom=tuple(item.with_whom), busy=item.busy, outfit=item.outfit,
                ambience=item.ambience, key=f"routine:{day.isoformat()}T{item.start}")


def tails(slots: Iterable[Slot]) -> list[Slot]:
    """The parts of yesterday's slots that run into today, moved to today's clock."""
    return [replace(slot, start=0, end=slot.end - DAY_MINUTES)
            for slot in slots if slot.end > DAY_MINUTES]


def _subtract(slot: Slot, taken: Sequence[Slot]) -> list[Slot]:
    pieces = [slot]
    for other in taken:
        rest = []
        for piece in pieces:
            if other.end <= piece.start or other.start >= piece.end:
                rest.append(piece)
                continue
            if other.start > piece.start:
                rest.append(replace(piece, end=other.start))
            if other.end < piece.end:
                rest.append(replace(piece, start=other.end))
        pieces = rest
    return [piece for piece in pieces if piece.end - piece.start >= MIN_PIECE_MIN]


def layered(*layers: Sequence[Slot]) -> list[Slot]:
    result: list[Slot] = []
    for layer in layers:
        added = [piece for slot in layer for piece in _subtract(slot, result)]
        result.extend(_subtract_each_other(added))
    return sorted(result, key=lambda slot: (slot.start, slot.end))


def _subtract_each_other(slots: list[Slot]) -> list[Slot]:
    """Within one layer an earlier-starting slot wins an overlap."""
    kept: list[Slot] = []
    for slot in sorted(slots, key=lambda s: (s.start, s.end)):
        kept.extend(_subtract(slot, kept))
    return kept


def build_agenda(day: date, life: PersonaLife, plans: dict[date, list[DayEvent]]) -> Agenda:
    yesterday = day - timedelta(days=1)
    today_plan = [event_slot(day, event) for event in plans.get(day, [])]
    yesterday_plan = [event_slot(yesterday, event) for event in plans.get(yesterday, [])]
    routine = routine_slots(life, day)
    if today_plan:
        # The plan decided the waking day (it may skip a run); the routine adds only the night.
        routine = [slot for slot in routine if slot.end > DAY_MINUTES]
    slots = layered(today_plan, tails(yesterday_plan), routine,
                    tails(routine_slots(life, yesterday)))
    return Agenda(day=day, slots=tuple(slots), planned=bool(today_plan))
