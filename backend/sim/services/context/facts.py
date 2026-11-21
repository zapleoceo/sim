"""Facts computed by code, never by the model (docs/context.md §2.3): the persona's local date,
weekday, time and part of day, her age, the client's local time, how long ago each side wrote,
counts. Pure functions of their input; "now" always comes from the Clock."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sim.services.capabilities import Capabilities

DEFAULT_TZ = "Europe/Kyiv"

MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def to_local(at: datetime, tz: str | None) -> datetime:
    """Wall clock in `tz`; a naive datetime is taken as UTC."""
    aware = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    return aware.astimezone(zone(tz))


def part_of_day(hour: int) -> str:
    if hour < 5:
        return "ночь"
    if hour < 12:
        return "утро"
    if hour < 17:
        return "день"
    if hour < 23:
        return "вечер"
    return "ночь"


def age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def years_word(n: int) -> str:
    return plural_ru(n, "год", "года", "лет")


def human_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def stamp(at: datetime, tz: str | None) -> str:
    """Timeline time: `21.11 20:30` in the persona's zone."""
    return f"{to_local(at, tz):%d.%m %H:%M}"


def human_gap(delta: timedelta) -> str:
    seconds = int(max(delta.total_seconds(), 0))
    if seconds < 60:
        return "меньше минуты"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} дн. {hours} ч" if hours else f"{days} дн."


def clock_duration(seconds: float | None) -> str:
    """`3:12` for a voice note or a call."""
    total = round(seconds or 0)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class FactsInput:
    now: datetime
    persona_tz: str
    birth_date: date | None = None
    client_tz: str | None = None
    first_contact_at: datetime | None = None
    last_client_at: datetime | None = None
    last_persona_at: datetime | None = None
    messages_count: int = 0
    calls_count: int = 0
    active_call: bool = False
    # What is being generated right now, e.g. ("видео «утро у окна»",).
    generations: tuple[str, ...] = ()
    capabilities: Capabilities | None = None


@dataclass(frozen=True)
class Facts:
    lines: tuple[str, ...]
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(f"- {line}" for line in self.lines)


def _yes(flag: bool) -> str:
    return "да" if flag else "нет"


def compute_facts(inp: FactsInput) -> Facts:
    local = to_local(inp.now, inp.persona_tz)
    today = local.date()
    part = part_of_day(local.hour)
    lines = [
        f"Сейчас у тебя: {WEEKDAYS[local.weekday()]}, {human_date(today)}, {local:%H:%M} "
        f"({part}), часовой пояс {zone(inp.persona_tz).key}.",
        f"Вчера было: {WEEKDAYS[(today - timedelta(days=1)).weekday()]}, "
        f"завтра будет: {WEEKDAYS[(today + timedelta(days=1)).weekday()]}.",
    ]
    values: dict[str, Any] = {
        "persona_local": local.isoformat(), "weekday": WEEKDAYS[local.weekday()],
        "part_of_day": part,
    }
    if inp.birth_date is not None:
        age = age_on(inp.birth_date, today)
        values["age"] = age
        birthday = (inp.birth_date.month, inp.birth_date.day) == (today.month, today.day)
        lines.append(f"Тебе {age} {years_word(age)} (родилась {human_date(inp.birth_date)})"
                     + (", сегодня твой день рождения." if birthday else "."))
    if inp.client_tz:
        client_local = to_local(inp.now, inp.client_tz)
        values["client_local"] = client_local.isoformat()
        lines.append(f"У собеседника сейчас {client_local:%H:%M}, "
                     f"{WEEKDAYS[client_local.weekday()]} ({zone(inp.client_tz).key}).")
    if inp.last_client_at is not None:
        gap = inp.now - inp.last_client_at
        values["since_client_s"] = int(gap.total_seconds())
        lines.append(f"Последнее сообщение собеседника было {human_gap(gap)} назад.")
    else:
        lines.append("Собеседник ещё ничего не писал.")
    if inp.last_persona_at is not None:
        gap = inp.now - inp.last_persona_at
        values["since_persona_s"] = int(gap.total_seconds())
        lines.append(f"Твоё последнее сообщение было {human_gap(gap)} назад.")
    else:
        lines.append("Твоих сообщений в этом чате ещё не было.")
    if inp.first_contact_at is not None:
        first = to_local(inp.first_contact_at, inp.persona_tz)
        values["first_contact"] = first.date().isoformat()
        lines.append(f"Переписка началась {human_date(first.date())} "
                     f"({human_gap(inp.now - inp.first_contact_at)} назад).")
    values["messages"] = inp.messages_count
    values["calls"] = inp.calls_count
    lines.append(f"Всего сообщений в чате: {inp.messages_count}, звонков: {inp.calls_count}.")
    if inp.active_call:
        values["active_call"] = True
        lines.append("Сейчас идёт звонок с собеседником.")
    if inp.generations:
        values["generations"] = list(inp.generations)
        lines.append("Уже готовишь: " + ", ".join(inp.generations)
                     + " — не обещай это ещё раз, отправится само.")
    if inp.capabilities is not None:
        caps = inp.capabilities
        values["capabilities"] = caps.as_dict()
        lines.append(
            f"Сейчас можно: голосовые — {_yes(caps.voice)}; фото из каталога — "
            f"{_yes(caps.photo)}; новые фото — {_yes(caps.photo_generate)}; видео из каталога"
            f" — {_yes(caps.video)}; новые видео — {_yes(caps.video_generate)}.")
    return Facts(lines=tuple(lines), values=values)
