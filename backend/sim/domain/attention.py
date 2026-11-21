"""The persona's attention («Внимание», docs/attention.md): when she looks at her phone and what
she does with what she sees there. Pure definitions shared by the API, the phone check, the
inbox and the calls.

The owner's settings live in `persona.attention` (JSONB) and are read only through
`attention_settings`: a broken or missing value is the default, so an old row never breaks a
check. The attention agent answers `AttentionPlan` — one decision per chat it was shown."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sim.domain.life import HHMM_PATTERN

PACE_MIN = 0.5
PACE_MAX = 2.0
DEFAULT_PACE = 1.0
REASON_MAX = 120
MAX_DECISIONS = 50


class AttentionMode(StrEnum):
    """How she treats notifications (the owner's one main choice)."""

    HUMAN = "human"             # «Как живой человек»: her life decides when she looks
    FREE_ONLY = "free_only"     # «Только когда свободна»: busy counts as free, away is kept
    INSTANT = "instant"         # «Всегда сразу»: answers like a bot, for tests


class AttentionAction(StrEnum):
    """What she does with one chat at a phone check."""

    REPLY_NOW = "reply_now"
    REPLY_LATER = "reply_later"                 # reads now, answers at `later`
    READ_ONLY = "read_only"                     # reads, does not answer
    LEAVE_UNREAD = "leave_unread"               # does not even open the chat
    CALL_BACK = "call_back"                     # outgoing calls do not exist: texts instead
    TEXT_AFTER_MISSED_CALL = "text_after_missed_call"


@dataclass(frozen=True)
class AttentionSettings:
    mode: AttentionMode = AttentionMode.HUMAN
    pace: float = DEFAULT_PACE                  # one multiplier on every sampled delay
    may_leave_read: bool = True                 # may read and not answer (or not open)
    after_missed_call: bool = True              # texts after a call she did not take
    night_dnd: bool = True                      # the routine's sleep is «не беспокоить»

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "mode": self.mode.value}

    def is_instant(self, chat_instant: bool = False) -> bool:
        """«Всегда сразу» for the persona, or the chat's own «Отвечать сразу» switch."""
        return chat_instant or self.mode == AttentionMode.INSTANT


DEFAULT_SETTINGS = AttentionSettings()


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _pace(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_PACE
    return min(max(float(value), PACE_MIN), PACE_MAX)


def attention_settings(stored: Mapping[str, Any] | None) -> AttentionSettings:
    """`persona.attention` with the defaults applied; broken values fall back silently."""
    data = stored if isinstance(stored, Mapping) else {}
    try:
        mode = AttentionMode(data.get("mode", DEFAULT_SETTINGS.mode))
    except ValueError:
        mode = DEFAULT_SETTINGS.mode
    return AttentionSettings(
        mode=mode, pace=_pace(data.get("pace", DEFAULT_PACE)),
        may_leave_read=_flag(data.get("may_leave_read"), DEFAULT_SETTINGS.may_leave_read),
        after_missed_call=_flag(data.get("after_missed_call"),
                                DEFAULT_SETTINGS.after_missed_call),
        night_dnd=_flag(data.get("night_dnd"), DEFAULT_SETTINGS.night_dnd),
    )


# --- the attention agent's answer --------------------------------------------------------------


class ChatDecision(BaseModel):
    """One chat: what she does with it and, for `reply_later`, when (her local «HH:MM»)."""

    model_config = ConfigDict(extra="forbid")

    chat_id: int
    action: AttentionAction
    later: str | None = Field(default=None, pattern=HHMM_PATTERN,
                              description="Её местное время ЧЧ:ММ для reply_later, иначе null.")
    reason: str = Field(default="", max_length=REASON_MAX,
                        description="Коротко по-русски, почему так.")

    @field_validator("later", mode="before")
    @classmethod
    def _later(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and value.strip().lower() in ("", "null")):
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: Any) -> str:
        return " ".join(str(value or "").split())[:REASON_MAX]


class AttentionPlan(BaseModel):
    """The attention agent's whole answer: a decision per chat it was shown."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[ChatDecision] = Field(default_factory=list, max_length=MAX_DECISIONS)
