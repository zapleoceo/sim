"""Communication journal: every change that affects how a dialog is led becomes a service line of
that dialog (`dialog_event`), shown in the feed between the messages and given to the persona.

One record says what changed (`kind`, from -> to), who did it (owner, operator, system or model)
and why. The reason is what the actor gave or what the code knows for a fact; when there is none
the field stays empty (the feed then shows no details). Nothing is invented and nothing here is
ever sent to the client.

Kinds and who writes them:
- `stage`, `status`: `FunnelService.record` (every funnel change of the client, in each of the
  client's dialogs);
- `ai_pause`: `AiPauseService` (the switch), an operator message, the owner writing from the phone;
- `reply_delay`: when the answer comes: put off by the persona's routine
  (`ReplyScheduler.schedule`), made due by «Ответить сейчас», and what happened to the
  unanswered messages when an AI pause was lifted;
- `urgent`: «Срочный» was switched on or off;
- `handoff`: the model or the system gave the dialog to an operator;
- `call_issue`: a call could not start or ended with a technical problem, with its cause
  (`sim.services.calls`); the persona reads it for a day;
- `call_steps`: «Ход звонка», every action of one call that shaped her answer (the pause before
  picking up, the voice session, each reviewed reply, how it ended) with its time since the
  ring (`sim.services.call_trace`). A multi-line technical record for the admin only: the
  persona never reads it;
- `resolved`: «Обработано»;
- `chat_cleared`, `passport_cleared`, `reset`: the wipes of `DialogReset`;
- `persona`, `strategy`, `model`: the persona, its strategy or its model were changed (in the
  persona's dialogs that had a message within `FANOUT_DAYS`);
- `passport`, `needs`, `notes`, `next_step`: what is known about the client changed.

The persona's media decisions have their own journal in the same feed and context
(`sim.services.media_decisions`): they are not repeated here.

The persona reads the records of the context window (`context_lines`), but nothing older than
the last wipe of the chat: after «Очистить чат» or `/reset` she starts from a clean page. Those
wipes also delete the dialog's older records (`DialogReset.clear_chat`), so the feed starts with
the wipe line."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AdminUser, Client, Dialog, DialogEvent, Persona
from sim.adapters.db.repositories.conversations import DialogEventRepo
from sim.clock import Clock, utcnow

STAGE = "stage"
STATUS = "status"
AI_PAUSE = "ai_pause"
REPLY_DELAY = "reply_delay"
HANDOFF = "handoff"
CALL_ISSUE = "call_issue"
CALL_STEPS = "call_steps"
RESOLVED = "resolved"
CHAT_CLEARED = "chat_cleared"
PASSPORT_CLEARED = "passport_cleared"
RESET = "reset"
PERSONA = "persona"
STRATEGY = "strategy"
MODEL = "model"
PASSPORT = "passport"
NEEDS = "needs"
NOTES = "notes"
NEXT_STEP = "next_step"
URGENT = "urgent"

WIPES = (CHAT_CLEARED, RESET)     # the persona's context starts after the last of these

AI_ON = "on"
AI_PAUSED = "paused"

OWNER = "owner"
OPERATOR = "operator"
SYSTEM_ACTOR = "system"
MODEL_ACTOR = "model"

VALUE_MAX = 500
REASON_MAX = 1000
STEPS_MAX = 8000          # a «Ход звонка» record keeps its lines and may be long
FEED_LIMIT = 500          # newest records shown in the dialog feed
CONTEXT_EVENTS = 20       # newest records of the window given to the persona
CONTEXT_VALUE_MAX = 160   # and how much of a long value or reason she reads
FANOUT_DAYS = 30          # a persona-wide change is journaled in dialogs active this recently
NO_REASON = "не указана"

KIND_LABELS = {
    CALL_ISSUE: "техническая проблема звонка", CALL_STEPS: "ход звонка",
    STAGE: "смена этапа", STATUS: "смена статуса", AI_PAUSE: "ИИ в диалоге",
    REPLY_DELAY: "время ответа", HANDOFF: "передача оператору", RESOLVED: "обработано",
    CHAT_CLEARED: "чат очищен", PASSPORT_CLEARED: "паспорт очищен",
    RESET: "сброс диалога командой /reset", PERSONA: "изменена персона",
    STRATEGY: "смена стратегии", MODEL: "смена модели", PASSPORT: "изменён паспорт",
    NEEDS: "потребности клиента", NOTES: "заметки", NEXT_STEP: "следующий шаг",
    URGENT: "срочность диалога",
}
ACTOR_LABELS = {OWNER: "владелец", OPERATOR: "оператор", SYSTEM_ACTOR: "система",
                MODEL_ACTOR: "модель"}
VALUE_LABELS = {AI_ON: "включён", AI_PAUSED: "на паузе"}
DEFAULT_MODEL = "маршрут по умолчанию"     # a persona without its own model
# Persona and strategy fields whose change reaches the conversation, as the journal names them.
# The persona's strategy and model are journaled as their own kinds.
PERSONA_FIELDS = {
    "agent_prompt": "промпт агента",
    "name": "имя", "routine": "распорядок", "language": "язык", "birth_date": "дата рождения",
    "timezone": "часовой пояс", "voice_mode": "голосовые ответы", "accept_calls": "приём звонков",
    "is_active": "активность",
}
STRATEGY_FIELDS = {
    "name": "название", "goal": "цель", "prompt": "сценарий", "offer": "оффер",
    "partner_name": "партнёр", "registration_url": "ссылка регистрации",
    "stage_playbook": "подсказки по этапам", "rules": "правила",
    "followup_hours": "напоминания",
}


def changed_fields(before: Mapping[str, object], after: Mapping[str, object],
                   labels: Mapping[str, str]) -> str:
    """The names of the listed fields that differ, "" when none does. A field missing in
    `after` was not sent and so not changed."""
    return ", ".join(label for field, label in labels.items()
                     if field in after and before.get(field) != after[field])


@dataclass(frozen=True)
class Who:
    """Who made the change: the kind of actor and, when known, a name to show."""

    actor: str
    name: str = ""


SYSTEM = Who(SYSTEM_ACTOR)
THE_MODEL = Who(MODEL_ACTOR)
THE_OWNER = Who(OWNER)
SOME_OPERATOR = Who(OPERATOR)


def admin_label(user: AdminUser) -> str:
    return user.display_name or (f"@{user.tg_username}" if user.tg_username else f"admin #{user.id}")


def admin_who(user: AdminUser | None) -> Who:
    """An admin as the actor of a record: owner rights make an owner, anyone else an operator."""
    if user is None:
        return SOME_OPERATOR
    return Who(OWNER if user.role.has_owner_rights else OPERATOR, admin_label(user)[:120])


def clip(value: object, limit: int = VALUE_MAX) -> str:
    return " ".join(str(value or "").split())[:limit]


def clip_lines(value: object, limit: int) -> str:
    """Like `clip`, but each line keeps its own place: a step-by-step record stays readable."""
    lines = (" ".join(line.split()) for line in str(value or "").splitlines())
    return "\n".join(line for line in lines if line)[:limit]


def changed_lines(old: str, new: str) -> tuple[str, str]:
    """(removed, added) lines of a rewritten text: what a passport update really changed."""
    before = [line.strip() for line in old.splitlines() if line.strip()]
    after = [line.strip() for line in new.splitlines() if line.strip()]
    removed = [line for line in before if line not in after]
    added = [line for line in after if line not in before]
    return clip(" / ".join(removed)), clip(" / ".join(added))


def event_line(event: DialogEvent) -> str:
    """One record as the persona reads it in her context (long values are shortened there)."""
    old = VALUE_LABELS.get(event.from_value, clip(event.from_value, CONTEXT_VALUE_MAX))
    new = VALUE_LABELS.get(event.to_value, clip(event.to_value, CONTEXT_VALUE_MAX))
    change = f"{old} → {new}" if old and new else (new or old)
    who = " ".join(filter(None, (ACTOR_LABELS.get(event.actor, event.actor), event.actor_name)))
    head = f"- {event.created_at.strftime('%Y-%m-%d %H:%M')} {KIND_LABELS.get(event.kind, event.kind)}"
    reason = clip(event.reason, CONTEXT_VALUE_MAX) or NO_REASON
    return f"{head}{': ' + change if change else ''}; кто: {who}; причина: {reason}"


class CommunicationJournal:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.session = session
        self.clock = clock
        self.events = DialogEventRepo(session)

    def event(self, dialog_id: int, kind: str, old: object, new: object, who: Who,
              reason: str, now: datetime) -> DialogEvent:
        return DialogEvent(
            dialog_id=dialog_id, kind=kind, from_value=clip(old), to_value=clip(new),
            actor=who.actor, actor_name=who.name[:120],
            reason=clip_lines(reason, STEPS_MAX) if kind == CALL_STEPS else clip(reason, REASON_MAX),
            created_at=now,
        )

    async def record(self, dialog_id: int, kind: str, *, old: object = "", new: object = "",
                     who: Who = SYSTEM, reason: str = "") -> DialogEvent:
        return await self.events.add(self.event(dialog_id, kind, old, new, who, reason, self.clock()))

    async def ai_switched(self, dialog: Dialog, paused: bool, who: Who = SYSTEM,
                          reason: str = "") -> None:
        """The `ai_pause` line, written by whoever is about to set `dialog.ai_paused` to
        `paused`; nothing when the dialog is in that state already."""
        if dialog.ai_paused == paused:
            return
        old, new = (AI_ON, AI_PAUSED) if paused else (AI_PAUSED, AI_ON)
        await self.record(dialog.id, AI_PAUSE, old=old, new=new, who=who, reason=reason)

    async def record_many(self, dialog_ids: Iterable[int], kind: str, *, old: object = "",
                          new: object = "", who: Who = SYSTEM, reason: str = "") -> int:
        now = self.clock()
        rows = [self.event(dialog_id, kind, old, new, who, reason, now) for dialog_id in dialog_ids]
        self.session.add_all(rows)
        await self.session.flush()
        return len(rows)

    async def record_for_client(self, client_id: int, kind: str, *, old: object = "",
                                new: object = "", who: Who = SYSTEM, reason: str = "") -> int:
        """A change of the client (stage, status, passport...) in each of the client's dialogs."""
        ids = (await self.session.scalars(
            select(Dialog.id).where(Dialog.client_id == client_id).order_by(Dialog.id))).all()
        return await self.record_many(ids, kind, old=old, new=new, who=who, reason=reason)

    async def record_for_personas(self, persona_ids: Iterable[int], kind: str, *,
                                  old: object = "", new: object = "", who: Who = SYSTEM,
                                  reason: str = "") -> int:
        """A persona-wide change in the dialogs of these personas that are still alive (a
        message within `FANOUT_DAYS`); a long-silent dialog gets no line."""
        ids = (await self.session.scalars(
            select(Dialog.id)
            .join(Client, Client.id == Dialog.client_id)
            .where(Client.persona_id.in_(list(persona_ids)),
                   Dialog.last_message_at >= self.clock() - timedelta(days=FANOUT_DAYS))
            .order_by(Dialog.id)
        )).all()
        return await self.record_many(ids, kind, old=old, new=new, who=who, reason=reason)

    async def feed(self, dialog_id: int, limit: int = FEED_LIMIT) -> list[DialogEvent]:
        """The dialog's newest records, oldest first (the feed merges them with the messages)."""
        return await self.events.recent(dialog_id, limit)

    async def context_lines(self, dialog_id: int, since: datetime) -> list[str]:
        """What the persona reads: the records since `since` and after the last wipe of the chat."""
        events = await self.events.since(dialog_id, since)
        wipes = [i for i, event in enumerate(events) if event.kind in WIPES]
        if wipes:
            events = events[wipes[-1] + 1:]
        # The course of a call is for the admin only; technical incidents remain in the feed,
        # but cease to prime later conversations.
        events = [e for e in events if e.kind != CALL_STEPS]
        events = [e for e in events if e.kind != CALL_ISSUE
                  or e.created_at >= self.clock() - timedelta(hours=24)]
        lines = [event_line(event) for event in events[-CONTEXT_EVENTS:]]
        if any(e.kind == CALL_ISSUE for e in events[-CONTEXT_EVENTS:]):
            lines.append("Технический итог звонка — факт только этого диалога. Учитывай дату UTC; "
                         "при уместности можно кратко упомянуть прерванный разговор. "
                         "Не выдумывай сетевую причину или проблему у клиента. "
                         "Не повторяй объяснение, если уже обсудили его; обязательной реплики нет.")
        return lines

    # ---- persona-wide changes ------------------------------------------------------------
    async def persona_edited(
        self, persona_id: int, before: Mapping[str, object], after: Mapping[str, object],
        who: Who, strategy_names: Mapping[object, str],
    ) -> None:
        """The persona was saved: a line per kind of change that reaches her dialogs (another
        strategy, another model, her own texts and switches)."""
        ids = [persona_id]
        if before.get("strategy_id") != after.get("strategy_id"):
            await self.record_for_personas(
                ids, STRATEGY, old=strategy_names.get(before.get("strategy_id"), ""),
                new=strategy_names.get(after.get("strategy_id"), ""), who=who)
        if before.get("model") != after.get("model"):
            await self.record_for_personas(
                ids, MODEL, old=before.get("model") or DEFAULT_MODEL,
                new=after.get("model") or DEFAULT_MODEL, who=who)
        changed = changed_fields(before, after, PERSONA_FIELDS)
        if changed:
            await self.record_for_personas(ids, PERSONA, new="изменено: " + changed, who=who)

    async def strategy_edited(
        self, strategy_id: int, before: Mapping[str, object], after: Mapping[str, object], who: Who,
    ) -> None:
        """The strategy was saved: one line in the dialogs of the personas that use it."""
        changed = changed_fields(before, after, STRATEGY_FIELDS)
        if not changed:
            return
        ids = (await self.session.scalars(
            select(Persona.id).where(Persona.strategy_id == strategy_id))).all()
        await self.record_for_personas(ids, STRATEGY, new="изменено: " + changed, who=who)
