"""The persona's life (docs/persona-life.md): her static world (`persona.life`), the events of a
planned day (`persona_day.events`) and the JSON the daily planner must return.

Pure pydantic models and checks: the API, the planner, the context and the scene all read the
same definitions. Times are local wall-clock "HH:MM" in the persona's time zone; an event whose
end is earlier than its start runs past midnight."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sim.domain.enums import ArcStatus

DAY_MINUTES = 24 * 60
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
PLAN_MIN_EVENTS = 6
PLAN_MAX_EVENTS = 14
DAY_MAX_EVENTS = 30
# An outfit may change only when the place changes, after a pause this long, or around an
# event she is away for (a run, a shower, sleep).
OUTFIT_CHANGE_GAP_MIN = 30
MAX_OUTFIT_CHANGES = 6

HHMM = Annotated[str, Field(pattern=HHMM_PATTERN, description="Local time HH:MM.")]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Weekday = Annotated[int, Field(ge=0, le=6, description="0 = Monday … 6 = Sunday.")]


class BusyLevel(StrEnum):
    """How reachable she is during an event (docs/persona-life.md, «Занятость»)."""

    FREE = "free"           # answers as usual
    PARTIAL = "partial"     # answers late and short
    AWAY = "away"           # phone not in hand (sleep, a run): answers after the event


class Ambience(StrEnum):
    """The sound of the place: background of voice notes and of generated videos."""

    NONE = "none"
    QUIET_HOME = "quiet_home"
    KITCHEN = "kitchen"
    STREET = "street"
    PARK = "park"
    CAFE = "cafe"
    CAR = "car"
    TRANSPORT = "transport"
    OFFICE = "office"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def minutes(hhmm: str) -> int:
    """`07:30` -> 450."""
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def hhmm(total: int) -> str:
    """450 -> `07:30`; minutes past midnight wrap around."""
    value = total % DAY_MINUTES
    return f"{value // 60:02d}:{value % 60:02d}"


def span(start: str, end: str) -> tuple[int, int]:
    """(start, end) in minutes; an end not after the start is on the next day."""
    first, last = minutes(start), minutes(end)
    return first, last if last > first else last + DAY_MINUTES


# --- the static world ------------------------------------------------------------------------


class RoutineSlot(_Strict):
    """One line of her usual week: on these weekdays, from `start` to `end`, she does this."""

    days: list[Weekday] = Field(min_length=1, max_length=7)
    start: HHMM
    end: HHMM
    activity: str = Field(min_length=1, max_length=300)
    place: str = Field(default="", max_length=200)
    with_whom: list[Name] = Field(default_factory=list, max_length=10)
    busy: BusyLevel = BusyLevel.FREE
    outfit: str = Field(default="", max_length=300)
    ambience: Ambience | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> RoutineSlot:
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


class Person(_Strict):
    name: Name
    relation: str = Field(default="", max_length=200)
    traits: str = Field(default="", max_length=1000)
    contact: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=1000)


class Hobby(_Strict):
    name: Name
    details: str = Field(default="", max_length=1000)
    frequency: str = Field(default="", max_length=120)


class Place(_Strict):
    name: Name
    kind: str = Field(default="", max_length=60)
    details: str = Field(default="", max_length=1000)


class PersonaLife(_Strict):
    """`persona.life`. Empty is valid: then there is no routine and nothing to plan from."""

    routine: list[RoutineSlot] = Field(default_factory=list, max_length=60)
    people: list[Person] = Field(default_factory=list, max_length=60)
    hobbies: list[Hobby] = Field(default_factory=list, max_length=40)
    places: list[Place] = Field(default_factory=list, max_length=40)

    @property
    def empty(self) -> bool:
        return not (self.routine or self.people or self.hobbies or self.places)


# --- a planned day ---------------------------------------------------------------------------


class DayEvent(_Strict):
    """One event of a planned day."""

    start: HHMM
    end: HHMM
    title: str = Field(min_length=1, max_length=200, description="What she does.")
    place: str = Field(default="", max_length=200)
    with_whom: list[Name] = Field(default_factory=list, max_length=10)
    busy: BusyLevel = BusyLevel.FREE
    details: str = Field(default="", max_length=600,
                         description="Small concrete incidents and details of this event.")
    outfit: str = Field(default="", max_length=300,
                        description="What she wears during this event (same as before unless "
                                    "she plausibly changed).")
    ambience: Ambience = Field(default=Ambience.NONE,
                               description="The sound of the place during this event.")


class ArcUpdate(_Strict):
    arc_id: int
    stage: str = Field(min_length=1, max_length=500)
    status: ArcStatus = ArcStatus.ACTIVE


class DayPlan(_Strict):
    """What the daily planner returns (`response_format: json_schema`)."""

    events: list[DayEvent] = Field(min_length=PLAN_MIN_EVENTS, max_length=PLAN_MAX_EVENTS)
    arc_updates: list[ArcUpdate] = Field(default_factory=list, max_length=10)
    yesterday_summary: str = Field(default="", max_length=1000)


def day_errors(events: list[DayEvent]) -> list[str]:
    """Times in order, no overlaps, only the last event may run past midnight."""
    errors: list[str] = []
    previous_end: int | None = None
    for i, event in enumerate(events):
        if event.start == event.end:
            errors.append(f"events[{i}]: начало и конец совпадают")
            continue
        first, last = span(event.start, event.end)
        if last > DAY_MINUTES and i != len(events) - 1:
            errors.append(f"events[{i}]: через полночь может идти только последнее событие")
        if previous_end is not None and first < previous_end:
            errors.append(f"events[{i}]: начинается раньше, чем кончилось предыдущее "
                          f"({event.start} < {hhmm(previous_end)}), события по порядку и без "
                          "наложений")
        previous_end = last
    return errors


def outfit_errors(events: list[DayEvent]) -> list[str]:
    """The planner keeps her clothes through the day: every event says what she wears, and the
    outfit changes only with a reason (another place, a pause, after being away)."""
    errors = [f"events[{i}]: не указано outfit" for i, event in enumerate(events)
              if not event.outfit.strip()]
    changes = 0
    for i in range(1, len(events)):
        before, after = events[i - 1], events[i]
        if _same(before.outfit, after.outfit) or not after.outfit.strip():
            continue
        changes += 1
        gap = minutes(after.start) - span(before.start, before.end)[1]
        reason = (not _same(before.place, after.place) or gap >= OUTFIT_CHANGE_GAP_MIN
                  or BusyLevel.AWAY in (before.busy, after.busy))
        if not reason:
            errors.append(f"events[{i}]: одежда сменилась без причины (то же место, без паузы) — "
                          "оставь ту же одежду или добавь событие, где она переодевается")
    if changes > MAX_OUTFIT_CHANGES:
        errors.append(f"одежда меняется {changes} раз за день — слишком часто")
    return errors


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())
