"""A few entries of Marina's real life seed (the same shape as the owner's
`marina_life_seed.json`, loaded by scripts/load_life.py) and helpers for the life tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sim.db.models import Persona, PersonaDay
from sim.domain.life import DayEvent
from sim.services.life.store import LifeState
from sim.services.life.world import life_of

SEED: dict[str, Any] = {
    "life": {
        "routine": [
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "23:00", "end": "06:30",
             "activity": "сон, телефон в другой комнате", "place": "дома", "with_whom": [],
             "busy": "away"},
            {"days": [0, 2, 4, 5], "start": "06:45", "end": "07:30",
             "activity": "пробежка 5 км", "place": "вдоль Эльбы или в Большом саду",
             "with_whom": [], "busy": "away"},
            {"days": [0, 1, 2, 3, 4], "start": "07:30", "end": "08:30",
             "activity": "кофе с корицей на балконе, новости", "place": "дома, балкон",
             "with_whom": ["Андрей"], "busy": "free"},
            {"days": [0, 1, 2, 3, 4, 5, 6], "start": "20:00", "end": "20:30",
             "activity": "созвон с Соней", "place": "дома", "with_whom": ["Соня"],
             "busy": "partial"},
            {"days": [5], "start": "14:00", "end": "18:00",
             "activity": "шребергартен мамы: грядки, иногда шашлык",
             "place": "садовый участок мамы", "with_whom": ["мама", "Андрей"],
             "busy": "partial"},
        ],
        "people": [
            {"name": "Андрей", "relation": "муж, с 2004",
             "traits": "инженер на заводе под Дрезденом, спокойный",
             "contact": "живут вместе", "notes": "водит серую Skoda Octavia"},
            {"name": "Бублик", "relation": "кот", "traits": "рыжий, наглый, ворует сыр",
             "contact": "дома", "notes": ""},
        ],
        "hobbies": [
            {"name": "бег", "details": "5 км вдоль Эльбы", "frequency": "4–5 раз в неделю"},
        ],
        "places": [
            {"name": "дом", "kind": "квартира",
             "details": "Дрезден, Йоханштадт, 4 этаж старого дома, балкон на луга у Эльбы"},
            {"name": "кофейня у дома", "kind": "кафе", "details": "работает там с ноутбуком"},
        ],
    },
    "arcs": [
        {"title": "Ремонт в ванной", "summary": "Хозяин согласился поменять ванну на душ.",
         "stage": "плитка стоит в коридоре", "status": "active", "ends_on": None},
    ],
}

TZ = "Europe/Berlin"
# The test clock (2026-11-21 18:30 UTC) is Saturday 19:30 in Berlin.
SATURDAY = date(2026, 11, 21)


def utc(day: date, hhmm: str) -> datetime:
    """Berlin wall-clock time on `day` as an aware UTC datetime."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=ZoneInfo(TZ)).astimezone(UTC)


def life_state(hhmm: str, *, day: date = SATURDAY,
               plans: dict[date, list[DayEvent]] | None = None, who: Persona | None = None,
               **kwargs: Any) -> LifeState:
    """A LifeState at Berlin `hhmm` on `day` with these planned days (no database)."""
    who = who or persona()
    now = utc(day, hhmm)
    days = {d: PersonaDay(persona_id=1, day=d, summary="",
                          events=[e.model_dump(mode="json") for e in events])
            for d, events in (plans or {}).items()}
    return LifeState(persona=who, life=life_of(who), now=now,
                     local=now.astimezone(ZoneInfo(TZ)), days=days, **kwargs)


def persona(**overrides: Any) -> Persona:
    fields: dict[str, Any] = {"name": "Марина", "timezone": TZ, "reply_delay_s": 8,
                              "life": SEED["life"], "appearance": "", "id": 1,
                              "voice_ambience_enabled": True}
    fields.update(overrides)
    return Persona(**fields)


def event(start: str, end: str, title: str, **fields: Any) -> DayEvent:
    return DayEvent(start=start, end=end, title=title, **fields)


def day_plan() -> list[DayEvent]:
    """A consistent Saturday: home clothes, a run, a shower, out to the garden, home."""
    home = "серый домашний свитер и тёмные леггинсы"
    out = "синие джинсы, бежевый свитер и тёмное пальто"
    return [
        event("07:00", "07:45", "пробежка вдоль Эльбы", place="вдоль Эльбы", busy="away",
              outfit="чёрный беговой костюм", ambience="park"),
        event("08:00", "09:00", "душ и завтрак с Андреем", place="дома, кухня",
              with_whom=["Андрей"], outfit=home, ambience="kitchen",
              details="Бублик стащил кусок сыра"),
        event("09:00", "11:00", "уборка и стирка", place="дома", outfit=home,
              ambience="quiet_home"),
        event("11:30", "12:30", "рынок с фрау Мюллер", place="рынок", busy="partial",
              with_whom=["фрау Мюллер"], outfit=out, ambience="street"),
        event("14:00", "18:00", "шребергартен мамы", place="садовый участок мамы",
              with_whom=["мама", "Андрей"], busy="partial", outfit=out, ambience="park"),
        event("19:00", "21:00", "ужин и сериал", place="дома", with_whom=["Андрей"],
              outfit=home, ambience="quiet_home", details="досмотрели третью серию"),
        event("21:30", "23:00", "чтение Канемана", place="дома, спальня", outfit=home,
              ambience="quiet_home"),
    ]
