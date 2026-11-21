"""Date facts for the daily planner, computed by code: weekday, season, public holidays of Germany
and Saxony (where Marina lives) and a few days everyone notices. A small static table; Easter
is computed (anonymous Gregorian algorithm)."""
from __future__ import annotations

from datetime import date, timedelta

from sim.services.context.facts import WEEKDAYS, human_date

SEASONS = ("зима", "зима", "весна", "весна", "весна", "лето", "лето", "лето", "осень", "осень",
           "осень", "зима")
# (month, day) -> name: fixed public holidays of Germany + Saxony, and notable days.
FIXED_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "Новый год (праздник, магазины закрыты)",
    (5, 1): "День труда (праздник, магазины закрыты)",
    (10, 3): "День германского единства (праздник, магазины закрыты)",
    (10, 31): "День Реформации (праздник в Саксонии, магазины закрыты)",
    (12, 24): "Сочельник (магазины до обеда)",
    (12, 25): "Рождество (праздник, магазины закрыты)",
    (12, 26): "второй день Рождества (праздник, магазины закрыты)",
    (12, 31): "канун Нового года",
}
# Days after Easter Sunday -> name.
EASTER_HOLIDAYS: dict[int, str] = {
    -2: "Страстная пятница (праздник, магазины закрыты)",
    0: "Пасха",
    1: "Пасхальный понедельник (праздник, магазины закрыты)",
    39: "Вознесение (праздник, магазины закрыты)",
    49: "Троица",
    50: "Духов понедельник (праздник, магазины закрыты)",
}
REPENTANCE_DAY = "День покаяния и молитвы (праздник в Саксонии, магазины закрыты)"


def easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def repentance_day(year: int) -> date:
    """Buß- und Bettag: the Wednesday before 23 November."""
    last = date(year, 11, 22)
    return last - timedelta(days=(last.weekday() - 2) % 7)


def holiday(day: date) -> str | None:
    if name := FIXED_HOLIDAYS.get((day.month, day.day)):
        return name
    if name := EASTER_HOLIDAYS.get((day - easter(day.year)).days):
        return name
    if day == repentance_day(day.year):
        return REPENTANCE_DAY
    return None


def season(day: date) -> str:
    return SEASONS[day.month - 1]


def date_facts(day: date) -> list[str]:
    """Lines about the planned day for the planner."""
    lines = [f"{WEEKDAYS[day.weekday()]}, {human_date(day)}, {season(day)}."]
    if day.weekday() >= 5:
        lines.append("Выходной.")
    if name := holiday(day):
        lines.append(f"Сегодня: {name}.")
    if name := holiday(day + timedelta(days=1)):
        lines.append(f"Завтра: {name}.")
    return lines
