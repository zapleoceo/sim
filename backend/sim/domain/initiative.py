"""When she may call first, and how a planned contact reads in her timeline
(docs/initiative.md). Pure: the service (sim/services/initiative.py) brings the numbers.

Her own calls are rare on purpose: a userbot that calls often looks like spam to Telegram and
like pressure to a person. The chat's «Звонит сама» switch is off by default."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

CALL_GAP = timedelta(hours=3)           # since her previous call in this chat, any outcome
CALLS_PER_DAY = 3                       # her calls in this chat per her local day
QUIET_FROM, QUIET_TO = 22, 9            # her local hours when she does not call
DIAL_DELAY_S = (20.0, 75.0)             # «давай созвонимся» -> she dials within a minute
RING_OUT_S = 40                         # how long her call rings before it counts as missed
MAX_TOPIC = 500
STALE_START = timedelta(minutes=10)     # a call that never reached the phone: failed

# Timeline keys of an `initiative` event (`chat_event.data`).
CONTACT_KEY = "contact_id"
TOPIC_KEY = "topic"
SOURCE_KEY = "source"
KIND_KEY = "kind"

NO_ANSWER_TOPIC = ("ты звонила ему ({topic}), он не взял трубку — напиши коротко, зачем "
                   "звонила")
NO_ANSWER_RESULTS = {"no_answer": "не взял трубку", "declined": "сбросил звонок",
                     "busy": "занято"}


@dataclass(frozen=True)
class CallOutState:
    """What decides whether she may call this client right now."""

    switched_on: bool                   # chat.calls_out
    unavailable: str                    # why calls can not run at all ("" = they can)
    local: datetime                     # her local time now
    last_call_at: datetime | None       # her previous call in this chat (UTC)
    calls_today: int                    # her calls in this chat since her local midnight
    pending: bool                       # a call of hers is already planned or ringing
    now: datetime


def call_out_block(state: CallOutState) -> str:
    """Why she must not call now, in words for the reply model; "" = she may."""
    if not state.switched_on:
        return "в этом чате звонить самой нельзя"
    if state.unavailable:
        return state.unavailable
    if state.pending:
        return "ты уже набираешь его"
    hour = state.local.hour
    if hour >= QUIET_FROM or hour < QUIET_TO:
        return "сейчас слишком поздно или рано для звонка"
    if state.last_call_at is not None and state.now - state.last_call_at < CALL_GAP:
        return "ты недавно уже звонила"
    if state.calls_today >= CALLS_PER_DAY:
        return "сегодня ты уже звонила достаточно"
    return ""


def initiative_line(topic: str) -> str:
    """How a due message contact reads in her timeline (a call speaks for itself)."""
    return f"(ты решила написать первой: {topic})"
