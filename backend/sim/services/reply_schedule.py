"""When the persona answers: a delay computed by code from her routine, the dialog and urgency.

A person at work, on the road or asleep does not answer at once. The delay is decided by rules,
never by a model (the model only writes the text of the answer):

1. The routine entry that covers "now" in her timezone (`persona_routine`) gets a class by the
   owner's word lists: free, sleep, work, road or busy (any other entry). Overlapping entries:
   the least reachable class wins.
2. Each class has "how often she looks at the chat" in minutes; 0 = not before the entry ends.
   The answer is due at the end of the entry or at the next look, whichever comes first.
3. An urgent dialog (an urgent word in the unanswered messages, or the client has waited longer
   than the threshold) is answered after the short urgent delay instead. Sleep is the exception:
   only an operator's «Срочный» / «Ответить сейчас» wakes her.
4. Free time, no routine, a dialog marked urgent or the feature switched off: no delay.

The plan lives in the pending `reply` job (`run_at` and `payload["delay"]`): the dialog card
shows it and the reply prompt gets one factual line about why the answer is late."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Client, Dialog
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.conversations import MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus
from sim.services import jobkinds
from sim.services.app_settings import AppSettings, AppSettingsService
from sim.services.dialog_state import turns
from sim.services.persona_clock import local_time
from sim.services.persona_routine import agenda, covers, minute_of, parse_routine

FREE, SLEEP, WORK, ROAD, BUSY = "free", "sleep", "work", "road", "busy"
RANK = {SLEEP: 3, WORK: 2, ROAD: 1, BUSY: 0}      # overlapping entries: the least reachable wins
WAS = {SLEEP: "спала", WORK: "была на работе", ROAD: "была в дороге", BUSY: "была занята"}
IS = {SLEEP: "спит", WORK: "на работе", ROAD: "в дороге", BUSY: "занята"}
URGENT_WORDS, URGENT_WAIT = "words", "waiting"
NOTE_MIN_WAIT = timedelta(minutes=5)   # a shorter wait is not a delay worth a line in the prompt


@dataclass(frozen=True)
class ScheduleRules:
    enabled: bool
    check_min: Mapping[str, int]              # class -> minutes between looks; 0 = until the entry ends
    class_words: Mapping[str, tuple[str, ...]]  # free / sleep / work / road -> word stems
    urgent_words: tuple[str, ...]
    urgent_delay_min: int
    urgent_wait_min: int                      # 0 = waiting never makes a dialog urgent


@dataclass(frozen=True)
class Incoming:
    text: str
    at: datetime          # naive UTC, when the client sent it


@dataclass(frozen=True)
class Plan:
    at: datetime                      # naive UTC: the answer is due not earlier
    delay: dict[str, Any] | None      # None = answer now; else the facts (JSON, kept in the job)


def stems(text: str) -> tuple[str, ...]:
    return tuple(w.strip().lower() for w in text.split(",") if w.strip())


def rules_from(cfg: AppSettings) -> ScheduleRules:
    return ScheduleRules(
        enabled=bool(cfg.reply_schedule_enabled),
        check_min={SLEEP: cfg.reply_check_sleep_min, WORK: cfg.reply_check_work_min,
                   ROAD: cfg.reply_check_road_min, BUSY: cfg.reply_check_busy_min},
        class_words={FREE: stems(cfg.reply_free_words), SLEEP: stems(cfg.reply_sleep_words),
                     WORK: stems(cfg.reply_work_words), ROAD: stems(cfg.reply_road_words)},
        urgent_words=stems(cfg.reply_urgent_words),
        urgent_delay_min=cfg.reply_urgent_delay_min,
        urgent_wait_min=cfg.reply_urgent_wait_min,
    )


def has_word(text: str, words: Sequence[str]) -> bool:
    """Does a word of the text start with one of these stems ("оплат" finds "по оплате", but
    "сон" does not find "персональная")."""
    low = text.lower()
    return any(re.search(r"(?<!\w)" + re.escape(word), low) for word in words)


def classify(what: str, rules: ScheduleRules) -> str:
    """The class of one routine entry by its text; the first list that matches, else busy."""
    for cls in (SLEEP, WORK, ROAD, FREE):
        if has_word(what, rules.class_words.get(cls, ())):
            return cls
    return BUSY


def urgency(waiting: Sequence[Incoming], now: datetime, rules: ScheduleRules) -> str | None:
    if has_word(" ".join(m.text for m in waiting), rules.urgent_words):
        return URGENT_WORDS
    if rules.urgent_wait_min > 0 and now - waiting[0].at >= timedelta(minutes=rules.urgent_wait_min):
        return URGENT_WAIT
    return None


def plan_reply(  # noqa: PLR0913
    now: datetime, routine: str, tz: str, waiting: Sequence[Incoming], rules: ScheduleRules,
    *, marked_urgent: bool = False,
) -> Plan:
    """The moment the unanswered messages get their one answer. `now` is naive UTC."""
    if not rules.enabled or marked_urgent or not waiting:
        return Plan(now, None)
    local = local_time(tz, now)
    minute = minute_of(local)
    current = [(classify(s.what, rules), s)
               for s in agenda(parse_routine(routine), local.date()) if covers(s, minute)]
    current = [(cls, s) for cls, s in current if cls != FREE]
    if not current:
        return Plan(now, None)
    cls, slot = max(current, key=lambda item: (RANK[item[0]], item[1].end))
    until = now.replace(second=0, microsecond=0) + timedelta(minutes=slot.end - minute)
    look = rules.check_min.get(cls, 0)
    at = until if look <= 0 else min(until, now + timedelta(minutes=look))
    urgent = urgency(waiting, now, rules)
    if urgent and cls != SLEEP:
        at = min(at, now + timedelta(minutes=rules.urgent_delay_min))
    return Plan(at, {"cls": cls, "what": slot.what, "until": until.isoformat(), "urgent": urgent})


def clock_text(moment: datetime, tz: str, today: datetime | None = None) -> str:
    """HH:MM on the persona's wall clock; with the day when it is not `today`'s."""
    local = local_time(tz, moment)
    if today is None or local.date() == today.date():
        return f"{local:%H:%M}"
    if (today.date() - local.date()).days == 1:
        return f"вчера в {local:%H:%M}"
    return f"{local:%d.%m} в {local:%H:%M}"


def until_of(delay: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(delay["until"]))
    except (KeyError, ValueError):
        return None


def reason_text(delay: Mapping[str, Any], tz: str) -> str:
    """Why the answer waits, for the dialog card: «на работе: консультации до 15:00»."""
    cls, until = delay.get("cls"), until_of(delay)
    if cls not in IS or until is None:
        return "после паузы ИИ" if delay.get("pause") else ""
    text = f"{IS[cls]}: {delay.get('what', '')} до {clock_text(until, tz)}"
    if delay.get("urgent") and cls != SLEEP:
        text += "; срочное, ответит раньше конца"
    return text


def delay_note(delay: Mapping[str, Any] | None, arrived: datetime, now: datetime, tz: str) -> str:
    """One line for the reply prompt about a late answer, built from facts: when the message
    came, when she answers and why (her routine, an AI pause). Empty when nothing was late."""
    if not delay or now - arrived < NOTE_MIN_WAIT:
        return ""
    local_now = local_time(tz, now)
    parts = [f"- Задержка ответа: сообщение собеседника пришло {at_text(arrived, tz, local_now)}, "
             f"ты отвечаешь в {local_now:%H:%M}."]
    cls, until = delay.get("cls"), until_of(delay)
    routine = cls in WAS and until is not None
    if delay.get("pause"):
        parts.append("В чате была пауза: ответы были остановлены, ты отвечаешь после её снятия.")
    if routine:
        parts.append(f"По распорядку ты {WAS[cls]}: {delay.get('what', '')} "
                     f"(до {clock_text(until, tz, local_now)}).")
        parts.append("Это обычная задержка, извиняться за неё не нужно.")
    elif delay.get("pause"):
        parts.append("Учитывай, что прошло время; о паузе собеседнику не рассказывай.")
    else:
        return ""
    return " ".join(parts)


def at_text(moment: datetime, tz: str, today: datetime) -> str:
    text = clock_text(moment, tz, today)
    return text if " в " in text else f"в {text}"


class ReplyScheduler:
    """Queues the ONE reply job of a dialog at the planned moment and re-plans it when the client
    writes again; «Ответить сейчас» makes it due at once."""

    def __init__(
        self, session: AsyncSession, *, clock: Clock = utcnow,
        settings_env: Mapping[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.clock = clock
        self.app_settings = AppSettingsService(session, settings_env)
        self.jobs = JobRepo(session)
        self.messages = MessageRepo(session)
        self.personas = PersonaRepo(session)

    async def plan(self, dialog: Dialog, client: Client, cfg: AppSettings) -> Plan:
        persona = await self.personas.get(client.persona_id)
        waiting = [Incoming(m.text, m.created_at)
                   for m in turns(await self.messages.unanswered(dialog.id))]
        if persona is None:
            return Plan(self.clock(), None)
        return plan_reply(self.clock(), persona.routine, persona.timezone, waiting, rules_from(cfg),
                          marked_urgent=dialog.urgent)

    async def schedule(self, dialog: Dialog, client: Client, *, after_pause: bool = False) -> bool:
        """One reply job per dialog. A burst of messages pushes it back by the debounce; a planned
        delay is kept (the moment she looks at the chat does not move with every message) and
        only shortened, e.g. by an urgent message. False: the AI does not answer this dialog."""
        if dialog.ai_paused or client.status is ClientStatus.REFUSED:
            return False
        cfg = await self.app_settings.load()
        now = self.clock()
        plan = await self.plan(dialog, client, cfg)
        delay = {**(plan.delay or {}), "pause": True} if after_pause else plan.delay
        earliest = now + timedelta(seconds=cfg.reply_debounce_s)
        key = jobkinds.reply_key(dialog.id)
        payload: dict[str, Any] = {"dialog_id": dialog.id, **({"delay": delay} if delay else {})}
        existing = await self.jobs.pending_by_key(key)
        if existing is None:
            await self.jobs.enqueue(jobkinds.REPLY, payload, max(earliest, plan.at), dedup_key=key)
            return True
        before = dict(existing.payload or {}).get("delay") or {}
        if before.get("cls"):       # she already "decided" when to look: never later than that
            existing.run_at = max(earliest, min(existing.run_at, plan.at))
        else:
            existing.run_at = max(existing.run_at, earliest, plan.at)
        if before.get("pause") and delay is not None:
            payload["delay"] = {**delay, "pause": True}
        elif before.get("pause"):
            payload["delay"] = {"pause": True}
        existing.payload = payload
        return True

    async def reply_now(self, dialog: Dialog) -> bool:
        """«Ответить сейчас»: the planned reply is due at once (the reason it waited stays in the
        job, the prompt still knows the answer is late). Does not touch an AI pause. False:
        nobody is waiting for an answer."""
        now = self.clock()
        key = jobkinds.reply_key(dialog.id)
        existing = await self.jobs.pending_by_key(key)
        if existing is not None:
            existing.run_at = min(existing.run_at, now)
            return True
        if not turns(await self.messages.unanswered(dialog.id)):
            return False
        await self.jobs.enqueue(jobkinds.REPLY, {"dialog_id": dialog.id}, now, dedup_key=key)
        return True

    async def waits_until(self, dialog_id: int) -> datetime | None:
        """When the dialog's delayed reply is due, or None when no delayed reply is planned."""
        job = await self.jobs.pending_by_key(jobkinds.reply_key(dialog_id))
        delay = dict(job.payload or {}).get("delay") if job is not None else None
        if not delay or not delay.get("cls") or job.run_at <= self.clock():
            return None
        return job.run_at

    async def pending(self, dialog: Dialog) -> dict[str, Any] | None:
        """The planned delayed reply for the dialog card: when and why."""
        if dialog.ai_paused or await self.waits_until(dialog.id) is None:
            return None
        job = await self.jobs.pending_by_key(jobkinds.reply_key(dialog.id))
        delay = dict(job.payload or {})["delay"]
        persona = await self.personas.get(dialog.client.persona_id)
        tz = persona.timezone if persona is not None else ""
        return {"at": job.run_at, "reason": reason_text(delay, tz), "cls": delay["cls"],
                "urgent": delay.get("urgent")}
