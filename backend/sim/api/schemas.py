"""Request bodies of docs/api.md as msgspec structs. A field left `UNSET` was not sent, so a
partial update touches only what the dashboard changed."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from litestar.exceptions import ClientException
from msgspec import UNSET, Meta, Struct, UnsetType, field

from sim.domain.agents import MAX_MAX_TOKENS, MAX_PROMPT_CHARS, MIN_MAX_TOKENS
from sim.domain.attention import PACE_MAX, PACE_MIN
from sim.domain.enums import ClonePart, LeadStage

Text4k = Annotated[str, Meta(min_length=1, max_length=4000)]
ModelId = Annotated[str, Meta(max_length=160)]
Tags = Annotated[list[Annotated[str, Meta(max_length=64)]], Meta(max_length=30)]


class AiToggleIn(Struct):
    enabled: bool


class ContactIn(Struct):
    """«Запланировать» (docs/initiative.md): she writes or calls at `due_at` about `topic`."""

    kind: Literal["message", "call"]
    topic: Annotated[str, Meta(min_length=1, max_length=500)]
    due_at: datetime


class PurgeRecordingsIn(Struct):
    """«Удалить старые записи»: older than `days`; null = the persona's keeping period."""

    days: Annotated[int, Meta(ge=0, le=3650)] | None = None


class TextIn(Struct):
    """Operator message or a note for the agent."""

    text: Text4k


class MediaPatchIn(Struct):
    caption: Annotated[str, Meta(max_length=1000)] | UnsetType = UNSET
    tags: Tags | UnsetType = UNSET
    is_reference: bool | UnsetType = UNSET
    is_room_ref: bool | UnsetType = UNSET


Short = Annotated[str, Meta(max_length=300)]


class PassportPatchIn(Struct):
    """The operator's edit of a client passport (docs/leads.md). Every structured field sent
    is locked for the passport writer; `locked_fields` replaces the lock list (to unlock)."""

    text: Annotated[str, Meta(max_length=12_000)] | UnsetType = UNSET
    name: Short | UnsetType = UNSET
    age: Annotated[int, Meta(ge=1, le=119)] | UnsetType | None = UNSET
    gender: Short | UnsetType = UNSET
    city: Short | UnsetType = UNSET
    country: Short | UnsetType = UNSET
    timezone: Annotated[str, Meta(max_length=64)] | UnsetType = UNSET
    occupation: Short | UnsetType = UNSET
    stage: LeadStage | UnsetType = UNSET
    temperature: Annotated[int, Meta(ge=0, le=100)] | UnsetType = UNSET
    next_step: Short | UnsetType = UNSET
    locked_fields: Annotated[list[Annotated[str, Meta(max_length=32)]],
                             Meta(max_length=20)] | UnsetType = UNSET


class ClientPatchIn(Struct):
    name: Annotated[str, Meta(max_length=200)] | UnsetType = UNSET
    phone: Annotated[str, Meta(max_length=32)] | UnsetType | None = UNSET
    notes: Annotated[str, Meta(max_length=20_000)] | UnsetType = UNSET
    timezone: Annotated[str, Meta(max_length=64)] | UnsetType | None = UNSET
    ai_enabled: bool | UnsetType = UNSET
    passport: PassportPatchIn | UnsetType = UNSET


class PersonaStateIn(Struct):
    """`PATCH /personas/{id}`: archive (false) or bring back (true)."""

    is_active: bool


class CloneIn(Struct):
    name: Annotated[str, Meta(min_length=1, max_length=120)]
    parts: Annotated[list[ClonePart], Meta(max_length=20)]


class TelegramLoginIn(Struct):
    """api_id / api_hash from my.telegram.org and an optional proxy. Never logged."""

    api_id: Annotated[int, Meta(ge=1, le=2_147_483_647)]
    api_hash: Annotated[str, Meta(min_length=8, max_length=64)]
    proxy: Annotated[str, Meta(max_length=300)] = ""


class PasswordIn(Struct):
    password: Annotated[str, Meta(min_length=1, max_length=256)]


class InstagramLoginIn(Struct, forbid_unknown_fields=True):
    """One way in (docs/channels.md): username + password, OR the browser's sessionid, OR an
    instagrapi settings JSON; the proxy is required (or SIM_IG_DEFAULT_PROXY). Never logged."""

    proxy: Annotated[str, Meta(max_length=300)] = ""
    username: Annotated[str, Meta(max_length=64)] = ""
    password: Annotated[str, Meta(max_length=256)] = ""
    sessionid: Annotated[str, Meta(max_length=512)] = ""
    settings_json: Annotated[str, Meta(max_length=200_000)] = ""


class WhatsAppLoginIn(Struct, forbid_unknown_fields=True):
    """Nothing is needed but the QR scan; `phone` is only a label until WhatsApp says it."""

    phone: Annotated[str, Meta(max_length=32)] = ""


class CodeIn(Struct):
    """A 2FA / challenge code; empty = «Повторить» after approving in the app."""

    code: Annotated[str, Meta(max_length=32)] = ""


class PersonaPatchIn(Struct):
    """Every editable column of `persona` except her agents and the guard's rule list (both
    on `PUT /personas/{id}/agents`). Unknown keys (id, timestamps) are ignored, so the
    dashboard may send back the whole object it received."""

    name: Annotated[str, Meta(min_length=1, max_length=120)] | UnsetType = UNSET
    gender: Annotated[str, Meta(max_length=32)] | UnsetType = UNSET
    language: Annotated[str, Meta(min_length=2, max_length=16)] | UnsetType = UNSET
    timezone: Annotated[str, Meta(min_length=1, max_length=64)] | UnsetType = UNSET
    birth_date: date | UnsetType | None = UNSET
    avatar_media_id: int | UnsetType | None = UNSET
    prompt: Annotated[str, Meta(max_length=200_000)] | UnsetType = UNSET
    appearance: Annotated[str, Meta(max_length=4000)] | UnsetType = UNSET
    voice_ambience_enabled: bool | UnsetType = UNSET
    voice_id: Annotated[str, Meta(max_length=64)] | UnsetType = UNSET
    voice_style: Annotated[str, Meta(max_length=4000)] | UnsetType = UNSET
    # sim/domain/speech.py: MIN_SPEED..MAX_SPEED
    voice_speed: Annotated[float, Meta(ge=0.8, le=1.5)] | UnsetType = UNSET
    call_recording: bool | UnsetType = UNSET
    call_recording_days: Annotated[int, Meta(ge=1, le=3650)] | UnsetType = UNSET
    ai_enabled: bool | UnsetType = UNSET
    voice_enabled: bool | UnsetType = UNSET
    photo_enabled: bool | UnsetType = UNSET
    video_enabled: bool | UnsetType = UNSET
    calls_enabled: bool | UnsetType = UNSET
    reply_delay_s: Annotated[int, Meta(ge=0, le=600)] | UnsetType = UNSET


ArcTitle = Annotated[str, Meta(min_length=1, max_length=200)]
ArcText = Annotated[str, Meta(max_length=4000)]
ArcStatusIn = Literal["active", "paused", "done"]


class ArcIn(Struct):
    title: ArcTitle
    summary: ArcText = ""
    stage: ArcText = ""
    status: ArcStatusIn = "active"
    started_on: date | None = None          # default: her local today
    ends_on: date | None = None


class ArcPatchIn(Struct):
    title: ArcTitle | UnsetType = UNSET
    summary: ArcText | UnsetType = UNSET
    stage: ArcText | UnsetType = UNSET
    status: ArcStatusIn | UnsetType = UNSET
    started_on: date | UnsetType = UNSET
    ends_on: date | UnsetType | None = UNSET


class ChatDayIn(Struct, forbid_unknown_fields=True):
    """The owner's edit of a chat's day summary (docs/memory.md): the day becomes `edited`."""

    summary: Annotated[str, Meta(min_length=1, max_length=30_000)]
    facts: Annotated[list[Annotated[str, Meta(max_length=300)]],
                     Meta(max_length=40)] = field(default_factory=list)


class CanonPatchIn(Struct):
    fact: Annotated[str, Meta(min_length=3, max_length=300)] | UnsetType = UNSET
    status: Literal["active", "rejected"] | UnsetType = UNSET


class AgentIn(Struct, forbid_unknown_fields=True):
    """One agent's settings (docs/agents.md). `model` null = the registry default, "" = not
    used; `key_tier` null = the default tier; an empty `prompt` = the default prompt;
    `max_tokens` null = the default limit."""

    model: ModelId | None = None
    key_tier: Literal["auto", "paid", "free"] | None = None
    prompt: Annotated[str, Meta(max_length=MAX_PROMPT_CHARS)] = ""
    max_tokens: Annotated[int, Meta(ge=MIN_MAX_TOKENS, le=MAX_MAX_TOKENS)] | None = None
    video_provider: Literal["google", "heygen"] | None = None


class AttentionIn(Struct, forbid_unknown_fields=True):
    """«Внимание» settings (docs/attention.md), edited on its agent card; all fields sent."""

    mode: Literal["human", "free_only", "instant"]
    pace: Annotated[float, Meta(ge=PACE_MIN, le=PACE_MAX)]
    may_leave_read: bool
    after_missed_call: bool
    night_dnd: bool


class AgentsIn(Struct):
    """`PUT /personas/{id}/agents`: the agents sent replace their stored settings (the others
    stay); `guard_rules` is the guard's rule list and `attention` the «Внимание» settings,
    both edited on their cards."""

    agents: Annotated[dict[str, AgentIn], Meta(max_length=32)]
    guard_rules: Annotated[str, Meta(max_length=20_000)] | UnsetType = UNSET
    attention: AttentionIn | UnsetType = UNSET


PERSONA_FIELDS: tuple[str, ...] = PersonaPatchIn.__struct_fields__


def sent_fields(data: Struct) -> dict[str, Any]:
    """The fields the client actually sent (not UNSET)."""
    values = ((f, getattr(data, f)) for f in data.__struct_fields__)
    return {f: v for f, v in values if v is not UNSET}


def check_timezone(name: str) -> None:
    """400 unless `name` is an IANA time zone (`Europe/Kyiv`)."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ClientException(detail=f"Неизвестный часовой пояс: {name}") from exc
