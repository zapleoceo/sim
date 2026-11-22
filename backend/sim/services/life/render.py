"""The life blocks of the agent's context (docs/context.md §2, docs/persona-life.md):

    # ТВОЙ РАСПОРЯДОК                 her usual week + people, hobbies, places
    # ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС    now, earlier today, later today, yesterday, tomorrow
    # Твои истории сейчас             active story arcs with their stage
    # Канон о тебе                    facts she invented about herself in chats

The persona prompt refers to the first two by these exact headings. Pure functions of
`LifeState`; compact and clipped to a character budget."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sim.domain.life import DAY_MINUTES, MOOD_WORDS, BusyLevel, Mood, PersonaLife, hhmm
from sim.services.context.facts import WEEKDAYS
from sim.services.context.render import one_line
from sim.services.life.agenda import Agenda, Slot
from sim.services.life.store import LifeState

ROUTINE_HEADING = "# ТВОЙ РАСПОРЯДОК"
NOW_HEADING = "# ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС"
ARCS_HEADING = "# Твои истории сейчас"
CANON_HEADING = "# Канон о тебе"
ROUTINE_MAX_CHARS = 9000
NOW_MAX_CHARS = 5000
ARCS_MAX_CHARS = 3000
CANON_MAX_CHARS = 4000
CLIPPED = "…"
NO_ROUTINE = ("(распорядок не задан — живи обычной жизнью и не противоречь тому, что уже "
              "говорила)")
DAY_CODES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
BUSY_WORDS = {
    BusyLevel.FREE: "свободна, можешь спокойно переписываться",
    BusyLevel.PARTIAL: "занята: отвечаешь не сразу и коротко",
    BusyLevel.AWAY: "недоступна, телефон не в руках",
}
BUSY_TAGS = {BusyLevel.FREE: "", BusyLevel.PARTIAL: " [занята]",
             BusyLevel.AWAY: " [недоступна]"}


@dataclass(frozen=True)
class LifeBlocks:
    routine: str
    now: str
    arcs: str
    canon: str

    def texts(self) -> list[str]:
        """Every non-empty block."""
        return [block for block in (self.routine, self.now, self.arcs, self.canon) if block]

    def shared(self) -> list[str]:
        """The non-empty blocks that do not change with the clock, most stable first: they go
        into the cached prefix of the prompt (docs/context.md §2). `now` goes at the end."""
        return [block for block in (self.routine, self.arcs, self.canon) if block]

    def snapshot(self) -> dict[str, Any]:
        return {"routine": self.routine, "now": self.now, "arcs": self.arcs,
                "canon": self.canon}


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - len(CLIPPED)].rstrip() + CLIPPED


def days_label(days: Iterable[int]) -> str:
    """[0, 1, 2, 3, 4] -> «пн–пт»; all seven -> «ежедневно»; [0, 2, 5] -> «пн, ср, сб»."""
    ordered = sorted(set(days))
    if len(ordered) == 7:
        return "ежедневно"
    runs: list[list[int]] = []
    for day in ordered:
        if runs and runs[-1][-1] == day - 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    parts = []
    for run in runs:
        if len(run) >= 3:
            parts.append(f"{DAY_CODES[run[0]]}–{DAY_CODES[run[-1]]}")
        else:
            parts.extend(DAY_CODES[day] for day in run)
    return ", ".join(parts)


def _where_who(place: str, with_whom: Sequence[str]) -> str:
    bits = [one_line(place)] if place.strip() else []
    if with_whom:
        bits.append("с " + ", ".join(with_whom))
    return f" ({'; '.join(bits)})" if bits else ""


def routine_lines(life: PersonaLife) -> list[str]:
    lines = []
    for item in sorted(life.routine, key=lambda s: (s.start, min(s.days))):
        lines.append(f"- {days_label(item.days)} {item.start}–{item.end} {one_line(item.activity)}"
                     f"{_where_who(item.place, item.with_whom)}{BUSY_TAGS[item.busy]}")
    return lines


def world_lines(life: PersonaLife) -> list[str]:
    lines: list[str] = []
    if life.people:
        lines.append("Люди в твоей жизни:")
        for person in life.people:
            bits = [b for b in (person.relation, person.traits, person.contact, person.notes)
                    if b.strip()]
            lines.append(f"- {person.name}" + (f" — {one_line('; '.join(bits))}" if bits else ""))
    if life.hobbies:
        lines.append("Увлечения:")
        for hobby in life.hobbies:
            when = f" ({one_line(hobby.frequency)})" if hobby.frequency.strip() else ""
            what = f": {one_line(hobby.details)}" if hobby.details.strip() else ""
            lines.append(f"- {hobby.name}{when}{what}")
    if life.places:
        lines.append("Места:")
        for place in life.places:
            kind = f" ({one_line(place.kind)})" if place.kind.strip() else ""
            what = f": {one_line(place.details)}" if place.details.strip() else ""
            lines.append(f"- {place.name}{kind}{what}")
    return lines + home_lines(life)


LOOK_NAMES = {"nails": "маникюр", "earrings": "серьги", "jewelry": "украшения и часы",
              "hair": "укладка", "other": "ещё"}
ROOM_NAMES = {"layout": "расстановка", "lamps": "светильники", "window_view": "вид из окна"}


def home_lines(life: PersonaLife) -> list[str]:
    """Her constant details and her one apartment: what she says about them in chat matches
    what her photos show (docs/persona-life.md, «Облик и дом»)."""
    lines: list[str] = []
    details = life.look.details()
    if details:
        lines.append("Твои постоянные детали (так на всех фото): "
                     + "; ".join(f"{LOOK_NAMES[key]} — {value}" for key, value in details))
    home = life.home
    if home.details.strip() or home.rooms:
        flat = f": {one_line(home.details)}" if home.details.strip() else ""
        lines.append(f"Твоя квартира (всегда одна и та же){flat}")
        for room in home.rooms:
            parts = ([one_line(room.details)] if room.details.strip() else []) + [
                f"{ROOM_NAMES[key]} — {value}" for key, value in room.fixed()]
            lines.append(f"- {room.name}" + (f": {'; '.join(parts)}" if parts else ""))
    return lines


def routine_block(life: PersonaLife) -> str:
    if life.empty:
        return f"{ROUTINE_HEADING}\n{NO_ROUTINE}"
    body = []
    if life.routine:
        body += ["Твоя обычная неделя (время местное):", *routine_lines(life)]
    body += world_lines(life)
    return clip(f"{ROUTINE_HEADING}\n" + "\n".join(body), ROUTINE_MAX_CHARS)


def slot_line(slot: Slot, *, details: bool = True) -> str:
    text = (f"- {hhmm(slot.start)}–{hhmm(slot.end)} {one_line(slot.title)}"
            f"{_where_who(slot.place, slot.with_whom)}")
    if details and slot.details.strip():
        text += f": {one_line(slot.details)}"
    return text


def _now_lines(state: LifeState, agenda: Agenda) -> list[str]:
    local = state.local
    minute = agenda.minute_of(local)
    slot = agenda.at(minute)
    source = "по плану дня" if agenda.planned else "по распорядку (плана на сегодня нет)"
    head = f"Сейчас {local:%H:%M}, {WEEKDAYS[local.weekday()]}, {source}."
    if slot is None:
        lines = [head, "Ты сейчас: свободное время, ничего не запланировано.",
                 f"Занятость: {BUSY_WORDS[BusyLevel.FREE]}."]
    else:
        lines = [head,
                 f"Ты сейчас: {one_line(slot.title)}{_where_who(slot.place, slot.with_whom)}, "
                 f"{hhmm(slot.start)}–{hhmm(slot.end)}.",
                 f"Занятость: {BUSY_WORDS[slot.busy]}."]
        if slot.details.strip():
            lines.append(f"Подробности: {one_line(slot.details)}")
        if slot.mood != Mood.CALM:
            why = f" — {one_line(slot.mood_why)}" if slot.mood_why.strip() else ""
            lines.append(f"Настроение: {MOOD_WORDS[slot.mood]}{why}.")
    if state.has_life:
        lines.append(f"На тебе сейчас: {state.scene().outfit}.")
    done = agenda.done(minute)
    lines.append("Сегодня уже было:" if done else "Сегодня уже было: ничего особенного.")
    lines += [slot_line(s) for s in done]
    later = agenda.later(minute)
    lines.append("Дальше сегодня:" if later else "Дальше сегодня: ничего не запланировано.")
    lines += [slot_line(s, details=False) for s in later]
    return lines


def _day_lines(state: LifeState, offset: int, label: str) -> list[str]:
    day = state.today + timedelta(days=offset)
    row = state.days.get(day)
    if offset < 0 and row is not None and row.summary.strip():
        return [f"{label} ({WEEKDAYS[day.weekday()]}): {one_line(row.summary)}"]
    agenda = state.agenda(day)
    slots = [s for s in agenda.slots if 0 <= s.start < DAY_MINUTES]
    if not slots:
        return [f"{label} ({WEEKDAYS[day.weekday()]}): ничего не известно."]
    source = "по плану" if agenda.planned else "по распорядку"
    return [f"{label} ({WEEKDAYS[day.weekday()]}, {source}):",
            *[slot_line(s, details=False) for s in slots if s.planned or not agenda.planned]]


def now_block(state: LifeState) -> str:
    agenda = state.agenda()
    lines = [*_now_lines(state, agenda), *_day_lines(state, -1, "Вчера"),
             *_day_lines(state, 1, "Завтра")]
    return clip(f"{NOW_HEADING}\n" + "\n".join(lines), NOW_MAX_CHARS)


def arcs_block(state: LifeState) -> str:
    if not state.arcs:
        return ""
    lines = []
    for arc in state.arcs:
        text = f"- {arc.title}"
        if arc.summary.strip():
            text += f": {one_line(arc.summary)}"
        if arc.stage.strip():
            text += f" Сейчас: {one_line(arc.stage)}"
        lines.append(text)
    return clip(f"{ARCS_HEADING}\n" + "\n".join(lines), ARCS_MAX_CHARS)


def canon_block(state: LifeState) -> str:
    if not state.canon:
        return ""
    lines = [f"- {one_line(item.fact)}" for item in state.canon]
    return clip(f"{CANON_HEADING} (ты это уже рассказывала — держись этого)\n"
                + "\n".join(lines), CANON_MAX_CHARS)


def life_blocks(state: LifeState) -> LifeBlocks:
    return LifeBlocks(routine=routine_block(state.life), now=now_block(state),
                      arcs=arcs_block(state), canon=canon_block(state))
