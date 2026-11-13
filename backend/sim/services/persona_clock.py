"""The persona's exact facts for this moment, computed by code, not by the model.

Models miscount ages, mix up weekdays and convert UTC to local time badly. The persona prompt
gets these facts ready-made in the dynamic context: her local date, weekday and time of day,
her age, birthday and zodiac sign, days until the next birthday, and from her routine where she
is now, was earlier and will be (`persona_routine`). The model only has to use them."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sim.services.persona_routine import routine_facts

DEFAULT_TZ = "Europe/Kyiv"

MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

# (month, day) of the first day of the NEXT sign, and the sign that ends the day before it.
ZODIAC_BOUNDS = (
    (1, 20, "Козерог"), (2, 19, "Водолей"), (3, 21, "Рыбы"), (4, 20, "Овен"), (5, 21, "Телец"),
    (6, 21, "Близнецы"), (7, 23, "Рак"), (8, 23, "Лев"), (9, 23, "Дева"), (10, 23, "Весы"),
    (11, 22, "Скорпион"), (12, 22, "Стрелец"),
)


def zodiac(d: date) -> str:
    for month, day, sign in ZODIAC_BOUNDS:
        if (d.month, d.day) < (month, day):
            return sign
    return "Козерог"


def age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def birthday_in(year: int, birth: date) -> date:
    """The birthday in a given year; 29 February falls on 1 March in a common year."""
    try:
        return birth.replace(year=year)
    except ValueError:
        return date(year, 3, 1)


def days_to_birthday(birth: date, today: date) -> int:
    next_bd = birthday_in(today.year, birth)
    if next_bd < today:
        next_bd = birthday_in(today.year + 1, birth)
    return (next_bd - today).days


def day_part(hour: int) -> str:
    if hour < 5:
        return "ночь"
    if hour < 12:
        return "утро"
    if hour < 17:
        return "день"
    return "вечер"


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def local_time(tz: str, now_utc: datetime) -> datetime:
    """The persona's wall clock. `now_utc` is naive UTC (the app clock) or aware."""
    aware = now_utc.replace(tzinfo=ZoneInfo("UTC")) if now_utc.tzinfo is None else now_utc
    return aware.astimezone(zone(tz))


def persona_facts(birth: date | None, tz: str, now_utc: datetime, routine: str = "") -> str:
    """Lines for the dynamic context. `now_utc` is naive UTC (the app clock)."""
    local = local_time(tz, now_utc)
    today = local.date()
    lines = [
        f"- Сейчас у тебя: {WEEKDAYS[local.weekday()]}, {human_date(today)}, {local:%H:%M} ({day_part(local.hour)})",
        f"- Вчера было: {WEEKDAYS[(today - timedelta(days=1)).weekday()]}, завтра: {WEEKDAYS[(today + timedelta(days=1)).weekday()]}",
    ]
    if birth is not None:
        days = days_to_birthday(birth, today)
        when = "сегодня твой день рождения" if days == 0 else f"до дня рождения {days} дн."
        lines.append(f"- Тебе {age_on(birth, today)} (родилась {human_date(birth)}), знак зодиака {zodiac(birth)}, {when}")
    lines += routine_facts(routine, local)
    return "\n".join(lines)
