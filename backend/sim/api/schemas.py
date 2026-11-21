"""Request bodies of docs/api.md as msgspec structs. A field left `UNSET` was not sent, so a
partial update touches only what the dashboard changed."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from litestar.exceptions import ClientException
from msgspec import UNSET, Meta, Struct, UnsetType

Text4k = Annotated[str, Meta(min_length=1, max_length=4000)]
ModelId = Annotated[str, Meta(max_length=160)]
Tags = Annotated[list[Annotated[str, Meta(max_length=64)]], Meta(max_length=30)]


class AiToggleIn(Struct):
    enabled: bool


class TextIn(Struct):
    """Operator message or a note for the agent."""

    text: Text4k


class MediaPatchIn(Struct):
    caption: Annotated[str, Meta(max_length=1000)] | UnsetType = UNSET
    tags: Tags | UnsetType = UNSET
    is_reference: bool | UnsetType = UNSET


class MemoryItemIn(Struct):
    fact: Annotated[str, Meta(min_length=1, max_length=500)]
    event_id: int | None = None
    at: str | None = None


class ClientPatchIn(Struct):
    name: Annotated[str, Meta(max_length=200)] | UnsetType = UNSET
    phone: Annotated[str, Meta(max_length=32)] | None | UnsetType = UNSET
    notes: Annotated[str, Meta(max_length=20_000)] | UnsetType = UNSET
    timezone: Annotated[str, Meta(max_length=64)] | None | UnsetType = UNSET
    ai_enabled: bool | UnsetType = UNSET
    memory: Annotated[list[MemoryItemIn], Meta(max_length=500)] | UnsetType = UNSET


class PersonaPatchIn(Struct):
    """Every editable column of `persona`. Unknown keys (id, timestamps) are ignored, so the
    dashboard may send back the whole object it received."""

    name: Annotated[str, Meta(min_length=1, max_length=120)] | UnsetType = UNSET
    gender: Annotated[str, Meta(max_length=32)] | UnsetType = UNSET
    language: Annotated[str, Meta(min_length=2, max_length=16)] | UnsetType = UNSET
    timezone: Annotated[str, Meta(min_length=1, max_length=64)] | UnsetType = UNSET
    birth_date: date | None | UnsetType = UNSET
    avatar_media_id: int | None | UnsetType = UNSET
    prompt: Annotated[str, Meta(max_length=200_000)] | UnsetType = UNSET
    model_reply: ModelId | UnsetType = UNSET
    model_guard: ModelId | UnsetType = UNSET
    model_vision: ModelId | UnsetType = UNSET
    model_transcribe: ModelId | UnsetType = UNSET
    model_memory: ModelId | UnsetType = UNSET
    model_tts: ModelId | UnsetType = UNSET
    model_image: ModelId | UnsetType = UNSET
    model_video: ModelId | UnsetType = UNSET
    model_live: ModelId | UnsetType = UNSET
    voice_id: Annotated[str, Meta(max_length=64)] | UnsetType = UNSET
    voice_style: Annotated[str, Meta(max_length=4000)] | UnsetType = UNSET
    guard_rules: Annotated[str, Meta(max_length=20_000)] | UnsetType = UNSET
    ai_enabled: bool | UnsetType = UNSET
    voice_enabled: bool | UnsetType = UNSET
    photo_enabled: bool | UnsetType = UNSET
    video_enabled: bool | UnsetType = UNSET
    calls_enabled: bool | UnsetType = UNSET
    reply_delay_s: Annotated[int, Meta(ge=0, le=600)] | UnsetType = UNSET


PERSONA_FIELDS: tuple[str, ...] = PersonaPatchIn.__struct_fields__
PERSONA_MODEL_FIELDS: tuple[str, ...] = tuple(
    f for f in PERSONA_FIELDS if f.startswith("model_"))


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
