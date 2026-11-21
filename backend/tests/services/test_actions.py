from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import (
    Chat,
    ChatEvent,
    Generation,
    LlmCall,
    MediaAsset,
    Message,
    Persona,
    ReplyRun,
)
from sim.domain.enums import (
    EventKind,
    GenerationKind,
    GenerationStatus,
    LlmPurpose,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    RunStatus,
)
from sim.domain.plan import GenerateSpec, PhotoAction, TextAction, VideoAction, VoiceAction
from sim.ports import PortError
from sim.services.actions import ActionContext, ApplyResult, handler_for
from sim.services.actions.media import PhotoHandler, VideoHandler
from sim.services.actions.text import TextHandler
from sim.services.actions.voice import Spoken, VoiceFailed, VoiceHandler
from sim.services.deps import Deps
from tests.services.conftest import Fakes
from tests.services.factories import all_rows, make_chat, make_media

SPEC = GenerateSpec(prompt="селфи у окна утром", caption="утро у окна", tags=["Утро", "дом"])


async def context(db: AsyncSession, deps: Deps) -> tuple[Chat, ActionContext]:
    chat = await make_chat(db)
    persona = await db.get(Persona, chat.persona_id)
    assert persona is not None
    run = ReplyRun(chat_id=chat.id, status=RunStatus.RUNNING)
    db.add(run)
    await db.flush()
    return chat, ActionContext(deps=deps, persona=persona, chat_id=chat.id, reply_run_id=run.id)


async def messages_of(db: AsyncSession, chat: Chat) -> list[Message]:
    return await all_rows(db, select(Message).where(Message.chat_id == chat.id)
                          .order_by(Message.id))


async def test_text_handler_queues_a_message(db: AsyncSession, deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    result = ApplyResult()
    await TextHandler().apply(db, TextAction(type="text", text="хай"), None, ctx, result)
    [message] = await messages_of(db, chat)
    assert (message.kind, message.text, message.status) == (MessageKind.TEXT, "хай",
                                                             MessageStatus.PENDING)
    assert message.reply_run_id == ctx.reply_run_id
    assert result.message_ids == [message.id]


async def test_voice_handler_tts_to_ogg_to_library(db: AsyncSession, deps: Deps,
                                                   fakes: Fakes) -> None:
    chat, ctx = await context(db, deps)
    action = VoiceAction(type="voice", text="приветик, ну как ты?")
    handler = VoiceHandler()

    prepared = await handler.prepare(action, ctx)
    assert isinstance(prepared, Spoken) and prepared.data.startswith(b"OGG")
    result = ApplyResult()
    await handler.apply(db, action, prepared, ctx, result)

    assert fakes.speech.calls == [{"text": "приветик, ну как ты?", "voice": "Vindemiatrix",
                                   "style": "тепло, по-домашнему", "model": "tts-model"}]
    [message] = await messages_of(db, chat)
    assert message.kind == MessageKind.VOICE and message.text == ""
    media = await db.get(MediaAsset, message.media_id)
    assert media is not None
    assert (media.kind, media.source, media.mime) == (MediaKind.AUDIO, MediaSource.GENERATED,
                                                      "audio/ogg")
    assert media.description == "приветик, ну как ты?" and media.duration_s == 2.0
    assert fakes.storage.files[media.storage_key] == prepared.data
    calls = await all_rows(db, select(LlmCall).where(LlmCall.reply_run_id == ctx.reply_run_id))
    assert [c.purpose for c in calls] == [LlmPurpose.TTS]


async def test_voice_failure_falls_back_to_text(db: AsyncSession, deps: Deps,
                                                fakes: Fakes) -> None:
    chat, ctx = await context(db, deps)
    fakes.speech.error = PortError("tts down")
    action = VoiceAction(type="voice", text="приветик")
    handler = VoiceHandler()

    prepared = await handler.prepare(action, ctx)
    assert prepared == VoiceFailed("tts down")
    result = ApplyResult()
    await handler.apply(db, action, prepared, ctx, result)

    [message] = await messages_of(db, chat)
    assert (message.kind, message.text) == (MessageKind.TEXT, "приветик")
    assert "tts down" in result.notes[0]


async def test_photo_from_the_catalog_goes_as_is(db: AsyncSession, deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    photo = await make_media(db, chat.persona_id, caption="селфи")
    result = ApplyResult()
    await PhotoHandler().apply(db, PhotoAction(type="photo", media_id=photo.id, caption="я"),
                               None, ctx, result)
    [message] = await messages_of(db, chat)
    assert (message.kind, message.media_id, message.text) == (MessageKind.PHOTO, photo.id, "я")
    assert result.generation_ids == []


async def test_photo_generate_starts_a_generation_with_references(db: AsyncSession,
                                                                  deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    refs = [await make_media(db, chat.persona_id, is_reference=True) for _ in range(2)]
    await make_media(db, chat.persona_id)                                   # not a reference
    await make_media(db, chat.persona_id, source=MediaSource.GENERATED, is_reference=True)
    result = ApplyResult()
    await PhotoHandler().apply(db, PhotoAction(type="photo", generate=SPEC, caption="смотри"),
                               None, ctx, result)

    [caption] = await messages_of(db, chat)
    assert (caption.kind, caption.text) == (MessageKind.TEXT, "смотри")
    [generation] = await all_rows(db, select(Generation).where(Generation.chat_id == chat.id))
    assert generation.kind == GenerationKind.PHOTO and generation.model == "image-model"
    assert generation.status == GenerationStatus.QUEUED
    assert generation.ref_media_ids == [ref.id for ref in refs]
    assert generation.tags == ["утро", "дом"] and generation.caption == "утро у окна"
    assert result.generation_ids == [generation.id]
    [event] = await all_rows(db, select(ChatEvent).where(
        ChatEvent.chat_id == chat.id, ChatEvent.kind == EventKind.GENERATION_STARTED))
    assert event.generation_id == generation.id
    assert event.data == {"kind": "photo", "caption": "утро у окна"}


async def test_video_generate_uses_the_video_model(db: AsyncSession, deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    result = ApplyResult()
    await VideoHandler().apply(db, VideoAction(type="video", generate=SPEC), None, ctx, result)
    [generation] = await all_rows(db, select(Generation).where(Generation.chat_id == chat.id))
    assert generation.kind == GenerationKind.VIDEO and generation.model == "video-model"
    assert await messages_of(db, chat) == []        # no caption -> nothing sent yet


def test_registry_has_a_handler_per_action() -> None:
    assert isinstance(handler_for(TextAction(type="text", text="a")), TextHandler)
    assert isinstance(handler_for(VoiceAction(type="voice", text="a")), VoiceHandler)
    assert isinstance(handler_for(PhotoAction(type="photo", media_id=1)), PhotoHandler)
    assert isinstance(handler_for(VideoAction(type="video", media_id=1)), VideoHandler)

    class Unknown(BaseModel):
        pass

    with pytest.raises(LookupError):
        handler_for(Unknown())
