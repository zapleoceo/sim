"""What she does with each chat at a phone check (docs/attention.md, «Решение»). Pure:

1. Rules settle the clear cases without a model: «Всегда сразу» (or the chat's «Отвечать
   сразу») — reply now; a promised answer that is due — reply now; only an edit of an answered
   message — read; a single fresh message in a lively exchange while she is free — reply now.
2. The rest goes to the attention agent as one JSON (`agent_input`); its answer is checked
   per chat (`from_agent`): an impossible action becomes the nearest possible one, a chat it
   skipped or a failed agent falls back to «answer later» at the end of what she is busy with
   (`fallback`). A message is never lost."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from sim.db.models import Persona
from sim.domain.attention import (
    AttentionAction,
    AttentionPlan,
    AttentionSettings,
    ChatDecision,
)
from sim.domain.life import BusyLevel
from sim.domain.passport import STAGE_LABELS
from sim.services.attention.notices import ChatNotice
from sim.services.attention.timing import Availability, fallback_at, later_at, local_hhmm
from sim.services.context.facts import WEEKDAYS, to_local
from sim.services.plan_check import extract_json

A = AttentionAction
RULE, AGENT, FALLBACK = "rule", "agent", "fallback"
ACTIVE_EXCHANGE = timedelta(minutes=5)      # she wrote this recently: they are chatting
FRESH = timedelta(minutes=2)                # and his message is this new
LEAVE_ALONE = frozenset({A.READ_ONLY, A.LEAVE_UNREAD})
MISSED_CALL_ACTIONS = frozenset({A.TEXT_AFTER_MISSED_CALL, A.CALL_BACK})
REPLYING = frozenset({A.REPLY_NOW, A.TEXT_AFTER_MISSED_CALL})     # a reply turn now
READS_NOW = frozenset({A.REPLY_LATER, A.READ_ONLY})                 # opens the chat, no turn
BUSY_WORDS = {BusyLevel.FREE: "free — свободна", BusyLevel.PARTIAL: "partial — занята",
              BusyLevel.AWAY: "away — телефон не в руках"}
URGENT_WORDS = ("срочно", "важно", "ответь", "перезвони", "позвони", "где ты", "алло")
ABOUT_CHARS = 400


@dataclass(frozen=True)
class Decision:
    """One chat: the action, why, when she answers (`reply_later`), who decided and, for a text
    after a missed call, the note the reply turn reads."""

    chat_id: int
    action: AttentionAction
    reason: str = ""
    later_at: datetime | None = None
    source: str = RULE
    note: str = ""


# --- 1. rules ----------------------------------------------------------------------------------


def lively(notice: ChatNotice, now: datetime) -> bool:
    """One fresh message right after her own: a fast exchange in progress."""
    waiting = notice.unanswered
    return (waiting is not None and waiting.count == 1 and notice.new_messages == 1
            and not notice.missed_calls and not notice.edits
            and notice.last_out_at is not None and now - notice.last_out_at <= ACTIVE_EXCHANGE
            and now - waiting.last_in_at <= FRESH)


def rule_decision(notice: ChatNotice, av: Availability, settings: AttentionSettings,
                  now: datetime, tz: str | None) -> Decision | None:
    """The clear cases; None = the agent decides."""
    chat = notice.chat_id
    if settings.is_instant(notice.instant):
        return Decision(chat, A.REPLY_NOW, "«Всегда сразу»")
    if not notice.has_news and notice.promise is not None:
        return Decision(chat, A.REPLY_NOW,
                        f"обещала ответить в {local_hhmm(notice.promise.at, tz)}")
    if notice.after_call:
        return Decision(chat, A.REPLY_NOW, "только что говорили по телефону")
    if notice.unanswered is None:
        return Decision(chat, A.READ_ONLY, "правка уже отвеченного сообщения")
    if av.level == BusyLevel.FREE and lively(notice, now):
        return Decision(chat, A.REPLY_NOW, "живая переписка")
    return None


# --- 2. the agent's input and answer -----------------------------------------------------------


def allowed_actions(settings: AttentionSettings) -> list[AttentionAction]:
    return [action for action in AttentionAction
            if (settings.may_leave_read or action not in LEAVE_ALONE)
            and (settings.after_missed_call or action not in MISSED_CALL_ACTIONS)]


def urgency_hints(notice: ChatNotice) -> list[str]:
    hints = []
    if notice.unanswered is not None and notice.unanswered.count >= 3:
        hints.append(f"{notice.unanswered.count} сообщений без ответа подряд")
    if len(notice.missed_calls) >= 2:
        hints.append(f"звонил {len(notice.missed_calls)} раза")
    text = " ".join(notice.texts).lower()
    hints += [f"пишет «{word}»" for word in URGENT_WORDS if word in text]
    if any(t.rstrip().endswith("?") for t in notice.texts):
        hints.append("есть вопрос")
    return hints


def chat_card(notice: ChatNotice, now: datetime, tz: str | None) -> dict[str, Any]:
    passport = notice.passport
    waiting = notice.unanswered
    since = (waiting.first_in_at or waiting.last_in_at) if waiting is not None else None
    return {
        "chat_id": notice.chat_id,
        "name": notice.client_name,
        "passport": {"stage": STAGE_LABELS.get(passport.stage, str(passport.stage)),
                     "temperature": passport.temperature, "next_step": passport.next_step,
                     "about": passport.text[:ABOUT_CHARS]},
        "last_messages": notice.lines,
        "last_from": notice.last_from,
        "unanswered": waiting.count if waiting is not None else 0,
        "waiting_min": int((now - since).total_seconds() // 60) if since is not None else 0,
        "missed_calls": [{"at": local_hhmm(call.at, tz), "status": call.status,
                          "while": call.activity} for call in notice.missed_calls],
        "edits": notice.edits,
        "hints": urgency_hints(notice),
        "promised": local_hhmm(notice.promise.at, tz) if notice.promise is not None else None,
    }


def agent_input(persona: Persona, av: Availability, notices: list[ChatNotice], now: datetime,
                settings: AttentionSettings) -> dict[str, Any]:
    tz = persona.timezone
    local = to_local(now, tz)
    return {
        "now": f"{WEEKDAYS[local.weekday()]} {local:%d.%m %H:%M}",
        "scene": {"activity": av.activity, "place": av.place, "with_whom": list(av.with_whom),
                  "busy": BUSY_WORDS[av.level],
                  "until": local_hhmm(av.ends_at, tz) if av.ends_at is not None else None},
        "allowed": [action.value for action in allowed_actions(settings)],
        "chats": [chat_card(notice, now, tz) for notice in notices],
    }


def parse_plan(text: str) -> AttentionPlan | None:
    """The agent's answer, or None when it is not the JSON of the schema."""
    try:
        return AttentionPlan.model_validate(json.loads(extract_json(text)))
    except (json.JSONDecodeError, ValidationError):
        return None


def missed_note(notice: ChatNotice) -> str:
    """What the reply turn is told about the calls she did not take."""
    activities = ", ".join(dict.fromkeys(call.activity for call in notice.missed_calls))
    calls = ("пропущенный звонок" if len(notice.missed_calls) == 1
             else f"пропущенные звонки ({len(notice.missed_calls)})")
    return f"{calls}, ты была занята: {activities}" if activities else calls


def fallback(notice: ChatNotice, av: Availability, settings: AttentionSettings, now: datetime,
             u: float, reason: str) -> Decision:
    return Decision(notice.chat_id, A.REPLY_LATER, reason,
                    later_at=fallback_at(av, now, settings.pace, u), source=FALLBACK)


def from_agent(notice: ChatNotice, chosen: ChatDecision | None, *, av: Availability,
               settings: AttentionSettings, now: datetime, tz: str | None, u: float,
               missing: str) -> Decision:
    """The agent's choice for one chat made possible; `missing` is the reason when it made
    none."""
    if chosen is None:
        return fallback(notice, av, settings, now, u, missing)
    action, reason = chosen.action, chosen.reason
    if action == A.CALL_BACK:                   # outgoing calls do not exist yet
        action = A.TEXT_AFTER_MISSED_CALL
    if action == A.TEXT_AFTER_MISSED_CALL and not notice.missed_calls:
        action = A.REPLY_NOW
    if action in LEAVE_ALONE and not settings.may_leave_read:
        return Decision(notice.chat_id, A.REPLY_LATER, reason,
                        later_at=fallback_at(av, now, settings.pace, u), source=AGENT)
    if action == A.REPLY_LATER:
        at = (later_at(chosen.later, now, tz) if chosen.later
              else fallback_at(av, now, settings.pace, u))
        if at > now:
            return Decision(notice.chat_id, A.REPLY_LATER, reason, later_at=at, source=AGENT)
        action = A.REPLY_NOW
    note = missed_note(notice) if action == A.TEXT_AFTER_MISSED_CALL else ""
    return Decision(notice.chat_id, action, reason, source=AGENT, note=note)


def decision_data(decision: Decision, notice: ChatNotice, persona: Persona) -> dict[str, Any]:
    """`chat_event.data` of an `attention` event: the dashboard line «Марина: прочитала,
    ответит позже (после рынка)» and the boundary of what she has seen."""
    later = decision.later_at
    data: dict[str, Any] = {
        "action": decision.action.value, "who": persona.name, "reason": decision.reason,
        "source": decision.source, "until_event_id": notice.until_event_id,
        "later": local_hhmm(later, persona.timezone) if later is not None else None,
        "later_at": later.isoformat() if later is not None else None,
    }
    if decision.note:
        data["note"] = decision.note
    return data
