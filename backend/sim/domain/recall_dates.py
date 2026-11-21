"""Russian relative dates for «А помнишь…?» (docs/memory.md): the reply model may name the days
to recall as ISO dates or as words («позавчера», «в прошлую субботу», «неделю назад», «на прошлой
неделе», «20 ноября»); code, not the model, turns them into dates of the persona's calendar.
Pure: `today` is her local date. Unknown words resolve to None, never to a guess."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MAX_SPAN_DAYS = 31

NUMBER_WORDS = {
    "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "пару": 2, "пара": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}
DAYS_AGO_WORDS = {"сегодня": 0, "вчера": 1, "позавчера": 2, "позапозавчера": 3}
# Weekday stems: «в среду», «в прошлую пятницу», «в воскресенье».
WEEKDAY_STEMS = ("понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресень")
MONTH_FORMS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
               "сентября", "октября", "ноября", "декабря")
# Words that say nothing about the day: «утром», «вечером», «тогда».
FILLER = frozenset({"утром", "днем", "днём", "вечером", "ночью", "тогда", "примерно", "где-то",
                    "около", "числа"})

_NUMBER = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"
_AGO = re.compile(_NUMBER + r"?\s*(день|дня|дней|неделю|недели|недель|месяц|месяца|месяцев)"
                  r"\s+назад$")
_FEW_DAYS_AGO = re.compile(r"(несколько|пару) дней назад$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$")
_DAY_MONTH = re.compile(r"^(\d{1,2})(?:-?го)?(?:\s+(" + "|".join(MONTH_FORMS) + r"))?$")
_WEEKDAY = re.compile(r"^(?:во?\s+)?(?:(прошл\w*|позапрошл\w*|эт\w*)\s+)?(\w+)$")
_WEEK = re.compile(r"^на\s+(прошлой|позапрошлой|этой)\s+неделе$")
_MONTH = re.compile(r"^в\s+(прошлом|этом)\s+месяце$")
_WEEKEND = re.compile(r"^(?:на|в)\s+(?:прошлых\s+|прошлые\s+)?выходн\w*$")


@dataclass(frozen=True)
class DateRange:
    first: date
    last: date

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


def normalize(text: str) -> str:
    words = text.lower().replace("ё", "е").strip(" \t\n.,;:!?…\"'«»()").split()
    return " ".join(word for word in words if word not in FILLER)


def _number(word: str | None) -> int:
    if not word:
        return 1
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word, 1)


def _around(day: date, slack: int) -> DateRange:
    return DateRange(day - timedelta(days=slack), day + timedelta(days=slack))


def _ago(text: str, today: date) -> DateRange | None:
    if few := _FEW_DAYS_AGO.search(text):
        if few.group(1) == "пару":
            return DateRange(today - timedelta(days=2), today - timedelta(days=2))
        return DateRange(today - timedelta(days=5), today - timedelta(days=3))
    match = _AGO.search(text)
    if match is None:
        return None
    n, unit = _number(match.group(1)), match.group(2)
    if unit.startswith("д"):
        return _around(today - timedelta(days=n), 0)
    if unit.startswith("недел"):
        return _around(today - timedelta(days=7 * n), 1)
    return _around(today - timedelta(days=30 * n), 3)


def _past_date(year: int, month: int, day: int, today: date) -> date | None:
    """That date this year, or last year when it is still ahead."""
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if found > today:
        try:
            found = date(year - 1, month, day)
        except ValueError:
            return None
    return found


def _explicit(text: str, today: date) -> date | None:
    if iso := _ISO.match(text):
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            return None
    if dotted := _DOTTED.match(text):
        if dotted[3]:
            year = int(dotted[3]) + (2000 if len(dotted[3]) == 2 else 0)
            try:
                return date(year, int(dotted[2]), int(dotted[1]))
            except ValueError:
                return None
        return _past_date(today.year, int(dotted[2]), int(dotted[1]), today)
    if day_month := _DAY_MONTH.match(text):
        day = int(day_month[1])
        if day_month[2]:
            return _past_date(today.year, MONTH_FORMS.index(day_month[2]) + 1, day, today)
        return _day_of_month(day, today)
    return None


def _day_of_month(day: int, today: date) -> date | None:
    """«20-го»: this month, or the month before when the 20th is still ahead."""
    month, year = today.month, today.year
    for _ in range(2):
        try:
            found = date(year, month, day)
        except ValueError:
            found = None
        if found is not None and found <= today:
            return found
        month, year = (12, year - 1) if month == 1 else (month - 1, year)
    return None


def _weekday(text: str, today: date) -> date | None:
    match = _WEEKDAY.match(text)
    if match is None:
        return None
    word = match.group(2)
    index = next((i for i, stem in enumerate(WEEKDAY_STEMS) if word.startswith(stem)), None)
    if index is None:
        return None
    which = match.group(1) or ""
    if which.startswith("эт"):
        this_week = today - timedelta(days=today.weekday() - index)
        return this_week if this_week <= today else None
    back = (today.weekday() - index) % 7 or 7          # strictly before today
    found = today - timedelta(days=back)
    if which.startswith("позапрошл"):
        found -= timedelta(days=7)
    return found


def _period(text: str, today: date) -> DateRange | None:
    monday = today - timedelta(days=today.weekday())
    if week := _WEEK.match(text):
        shift = {"этой": 0, "прошлой": 1, "позапрошлой": 2}[week[1]]
        first = monday - timedelta(days=7 * shift)
        return DateRange(first, min(first + timedelta(days=6), today))
    if month := _MONTH.match(text):
        first_this = today.replace(day=1)
        if month[1] == "этом":
            return DateRange(first_this, today)
        last = first_this - timedelta(days=1)
        return DateRange(last.replace(day=1), last)
    if _WEEKEND.match(text):
        saturday = today - timedelta(days=(today.weekday() - 5) % 7 or 7)
        return DateRange(saturday, saturday + timedelta(days=1))
    return None


def resolve_phrase(text: str, today: date) -> DateRange | None:
    """The days a phrase names, or None when it names none."""
    words = normalize(text)
    if not words:
        return None
    if words in DAYS_AGO_WORDS:
        day = today - timedelta(days=DAYS_AGO_WORDS[words])
        return DateRange(day, day)
    if (period := _period(words, today)) is not None:
        return period
    if (ago := _ago(words, today)) is not None:
        return ago
    bare = re.sub(r"^(в|во|на|за)\s+", "", words)
    single = _explicit(bare, today) or _weekday(words, today)
    return DateRange(single, single) if single is not None else None


def resolve_range(date_from: str, date_to: str, today: date) -> DateRange | None:
    """The recalled period: `date_from` (and `date_to` for a period), never after today and
    at most `MAX_SPAN_DAYS` long (the newest days are kept)."""
    start = resolve_phrase(date_from, today) if date_from.strip() else None
    end = resolve_phrase(date_to, today) if date_to.strip() else None
    if start is None and end is None:
        return None
    first = min(r.first for r in (start, end) if r is not None)
    last = min(max(r.last for r in (start, end) if r is not None), today)
    if first > last:
        return None
    first = max(first, last - timedelta(days=MAX_SPAN_DAYS - 1))
    return DateRange(first, last)
