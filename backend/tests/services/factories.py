"""Rows for service tests, created directly with the models (flushed, not committed)."""
from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import (
    Channel,
    Chat,
    ChatEvent,
    Client,
    ClientIdentity,
    MediaAsset,
    Message,
    Persona,
    PersonaSticker,
)
from sim.domain.enums import (
    Author,
    ChannelKind,
    ChannelStatus,
    Direction,
    EventKind,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    UnderstandingStatus,
)
from sim.events import HIDDEN_FROM_AGENT

_seq = itertools.count(1)

# Agent key -> model (sim/domain/agents.py). The daily planner is off unless a test turns it on.
PERSONA_MODELS: dict[str, str] = {
    "reply": "reply-model", "guard": "guard-model", "vision": "vision-model",
    "transcribe": "stt-model", "memory": "memory-model", "tts": "tts-model",
    "image": "image-model", "video": "video-model", "live": "live-model", "life": "",
    "passport": "passport-model",
}


def agents_of(models: Mapping[str, str]) -> dict[str, Any]:
    """`persona.agents` with only these models chosen."""
    return {key: {"model": model} for key, model in models.items()}


async def make_persona(db: AsyncSession, *, models: Mapping[str, str] | None = None,
                       **overrides: Any) -> Persona:
    """`models` changes some agents' models over `PERSONA_MODELS`."""
    fields: dict[str, Any] = {
        "name": "Марина", "gender": "female", "language": "ru", "timezone": "Europe/Kyiv",
        "birth_date": date(2001, 3, 3), "prompt": "Ты Марина, тебе нравится болтать.",
        "guard_rules": "Не называть фамилию.", "ai_enabled": True, "voice_enabled": True,
        "photo_enabled": True, "video_enabled": True, "calls_enabled": True,
        "reply_delay_s": 8, "voice_id": "Vindemiatrix", "voice_style": "тепло, по-домашнему",
        "agents": agents_of({**PERSONA_MODELS, **(models or {})}),
    }
    fields.update(overrides)
    persona = Persona(**fields)
    db.add(persona)
    await db.flush()
    return persona


async def make_channel(db: AsyncSession, persona: Persona) -> Channel:
    channel = Channel(persona_id=persona.id, kind=ChannelKind.TELEGRAM_USER, title="Марина",
                      status=ChannelStatus.CONNECTED)
    db.add(channel)
    await db.flush()
    return channel


async def make_chat(db: AsyncSession, persona: Persona | None = None, *, client_ai: bool = True,
                    chat_ai: bool = True, name: str = "Дима",
                    external_id: str | None = None) -> Chat:
    persona = persona or await make_persona(db)
    channel = await make_channel(db, persona)
    client = Client(persona_id=persona.id, name=name, username="dima", ai_enabled=client_ai,
                    passport={})
    db.add(client)
    await db.flush()
    db.add(ClientIdentity(client_id=client.id, channel_kind=ChannelKind.TELEGRAM_USER,
                          external_id=external_id or f"10{next(_seq)}"))
    chat = Chat(persona_id=persona.id, client_id=client.id, channel_id=channel.id,
                ai_enabled=chat_ai, needs_operator=False, unread_count=0, summary="")
    db.add(chat)
    await db.flush()
    return chat


async def olya_chat(db: AsyncSession) -> Chat:
    """A chat of another persona (persona names are unique)."""
    return await make_chat(db, await make_persona(db, name="Оля", prompt="Ты Оля."), name="Петя")


async def make_sticker(db: AsyncSession, chat: Chat, *, emoji: str = "😂",
                       set_title: str = "Утята", set_name: str = "ducks", faved: bool = False,
                       position: int = 0, doc_id: int | None = None,
                       channel_id: int | None = None) -> PersonaSticker:
    """One of her account's stickers on the chat's channel (docs/stickers.md)."""
    sticker = PersonaSticker(persona_id=chat.persona_id,
                             channel_id=channel_id or chat.channel_id,
                             doc_id=doc_id if doc_id is not None else 9_000_000 + next(_seq),
                             set_id=77, set_access_hash=88, set_name=set_name,
                             set_title=set_title, emoji=emoji, faved=faved, position=position)
    db.add(sticker)
    await db.flush()
    return sticker


DEFAULT_MIMES = {MediaKind.PHOTO: "image/jpeg", MediaKind.VIDEO: "video/mp4",
                 MediaKind.AUDIO: "audio/ogg"}


async def make_media(db: AsyncSession, persona_id: int | None, *,
                     kind: MediaKind = MediaKind.PHOTO,
                     source: MediaSource = MediaSource.LIBRARY, caption: str = "",
                     tags: list[str] | None = None, is_reference: bool = False,
                     mime: str | None = None, file_name: str = "", description: str = "",
                     understanding: UnderstandingStatus = UnderstandingStatus.SKIPPED,
                     storage: Any = None, data: bytes = b"data",
                     duration_s: float | None = None) -> MediaAsset:
    mime = mime or DEFAULT_MIMES.get(kind, "application/octet-stream")
    if storage is not None:
        key = await storage.put(data, mime=mime, prefix=source.value)
    else:
        key = f"test/{next(_seq)}"
    asset = MediaAsset(persona_id=persona_id, source=source, kind=kind, is_reference=is_reference,
                       storage_key=key, mime=mime, file_name=file_name, size_bytes=len(data),
                       sha256="0" * 64, caption=caption, tags=tags or [],
                       description=description, understanding=understanding,
                       duration_s=duration_s)
    db.add(asset)
    await db.flush()
    return asset


async def add_event(db: AsyncSession, chat: Chat, kind: EventKind, *, at: datetime,
                    message: Message | None = None, data: dict[str, Any] | None = None,
                    in_context: bool | None = None, **refs: Any) -> ChatEvent:
    visible = kind not in HIDDEN_FROM_AGENT if in_context is None else in_context
    event = ChatEvent(chat_id=chat.id, kind=kind, in_context=visible, created_at=at,
                      message_id=message.id if message is not None else None,
                      media_id=message.media_id if message is not None else None,
                      actor="system", data=data or {}, **refs)
    db.add(event)
    await db.flush()
    return event


async def add_message(db: AsyncSession, chat: Chat, text: str = "", *, at: datetime,
                      direction: Direction = Direction.IN, kind: MessageKind = MessageKind.TEXT,
                      media: MediaAsset | None = None, author: Author | None = None,
                      status: MessageStatus | None = None) -> tuple[Message, ChatEvent]:
    incoming = direction == Direction.IN
    message = Message(
        chat_id=chat.id, direction=direction,
        author=author or (Author.CLIENT if incoming else Author.PERSONA), kind=kind, text=text,
        media_id=media.id if media is not None else None,
        status=status or (MessageStatus.RECEIVED if incoming else MessageStatus.SENT),
        external_id=str(next(_seq)), sent_at=at,
    )
    db.add(message)
    await db.flush()
    event = await add_event(db, chat, EventKind.MESSAGE_IN if incoming else EventKind.MESSAGE_OUT,
                            at=at, message=message)
    return message, event


def plan_json(*messages: dict[str, Any], remember: list[str] | None = None) -> str:
    return json.dumps({"messages": list(messages),
                       "remember": [{"fact": fact} for fact in remember or []]},
                      ensure_ascii=False)


def ok_verdict() -> str:
    return json.dumps({"ok": True, "reason": ""})


def block_verdict(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason}, ensure_ascii=False)


async def fresh[T](db: AsyncSession, obj: T) -> T:
    await db.refresh(obj)
    return obj


async def all_rows(db: AsyncSession, query: Select[Any]) -> list[Any]:
    result = await db.execute(query.execution_options(populate_existing=True))
    return list(result.scalars())
