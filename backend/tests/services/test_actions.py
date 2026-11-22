from __future__ import annotations

from dataclasses import replace

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
from sim.domain.plan import (
    GenerateSpec,
    PhotoAction,
    TextAction,
    VideoAction,
    VideoNoteAction,
    VoiceAction,
    tts_style,
)
from sim.domain.writing import DEFAULT_MODE
from sim.ports import PortError
from sim.services.actions import ActionContext, ApplyResult, handler_for
from sim.services.actions.media import (
    PhotoHandler,
    Rounded,
    RoundFailed,
    VideoHandler,
    VideoNoteHandler,
)
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


async def test_text_goes_through_her_phone(db: AsyncSession, deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    steady = replace(DEFAULT_MODE, typo_rate=0.0, lowercase_rate=0.0)
    result = ApplyResult()
    await TextHandler().apply(db, TextAction(type="text", text="Спальня — это личное…"), None,
                              replace(ctx, writing=steady), result)
    [message] = await messages_of(db, chat)
    assert message.text == "Спальня - это личное..."


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
                                   "style": "тепло, по-домашнему", "model": "tts-model",
                                   "key_tier": "paid"}]
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


async def test_voice_mood_goes_before_the_usual_manner(db: AsyncSession, deps: Deps,
                                                      fakes: Fakes) -> None:
    _chat, ctx = await context(db, deps)
    action = VoiceAction(type="voice", text="ой, ну ты даёшь", style="весело")
    await VoiceHandler().prepare(action, ctx)
    assert fakes.speech.calls[-1]["style"] == "весело, тепло, по-домашнему"


async def test_her_voice_speed_goes_to_the_recording(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes) -> None:
    """persona.voice_speed, kept within the form's range (sim/domain/speech.py)."""
    _chat, ctx = await context(db, deps)
    action = VoiceAction(type="voice", text="приветик")
    await VoiceHandler().prepare(action, ctx)
    ctx.persona.voice_speed = 1.3
    await VoiceHandler().prepare(action, ctx)
    ctx.persona.voice_speed = 4.0
    await VoiceHandler().prepare(action, ctx)
    assert fakes.tools.speeds == [1.0, 1.3, 1.5]


def test_tts_style_is_short_and_skips_blanks() -> None:
    assert tts_style("тепло", "") == "тепло"
    assert tts_style("", " устало ") == "устало"
    assert len(tts_style("а" * 60, "б" * 40)) == 80


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
    # A generated photo the owner picked as her face is a reference too, after the uploaded ones.
    best = await make_media(db, chat.persona_id, source=MediaSource.GENERATED, is_reference=True)
    await make_media(db, chat.persona_id, kind=MediaKind.VIDEO, is_reference=True)   # not a photo
    result = ApplyResult()
    await PhotoHandler().apply(db, PhotoAction(type="photo", generate=SPEC, caption="смотри"),
                               None, ctx, result)

    [caption] = await messages_of(db, chat)
    assert (caption.kind, caption.text) == (MessageKind.TEXT, "смотри")
    [generation] = await all_rows(db, select(Generation).where(Generation.chat_id == chat.id))
    assert generation.kind == GenerationKind.PHOTO and generation.model == "image-model"
    assert generation.status == GenerationStatus.QUEUED
    assert generation.ref_media_ids == [ref.id for ref in refs] + [best.id]
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


# --- video notes («кружок») and statuses -----------------------------------------------------

NOTE_SPEC = GenerateSpec(prompt="машу рукой на кухне", caption="привет с кухни", tags=[],
                         speech="Дима, привет! Как прошёл день?")


async def test_video_note_from_the_catalog_is_cut_to_a_circle(db: AsyncSession, deps: Deps,
                                                              fakes: Fakes) -> None:
    chat, ctx = await context(db, deps)
    clip = await make_media(db, chat.persona_id, kind=MediaKind.VIDEO, caption="у моря",
                            tags=["море"], storage=fakes.storage, data=b"CLIP")
    action = VideoNoteAction(type="video_note", media_id=clip.id)
    handler = handler_for(action)
    assert isinstance(handler, VideoNoteHandler) and handler.busy(action) is None

    prepared = await handler.prepare(action, ctx)
    assert isinstance(prepared, Rounded) and prepared.data == b"ROUND:CLIP"
    result = ApplyResult()
    await handler.apply(db, action, prepared, ctx, result)

    [message] = await messages_of(db, chat)
    assert (message.kind, message.text) == (MessageKind.VIDEO_NOTE, "")
    note = await db.get(MediaAsset, message.media_id)
    assert note is not None and note.id != clip.id
    assert (note.kind, note.source, note.mime) == (MediaKind.VIDEO, MediaSource.GENERATED,
                                                   "video/mp4")
    assert note.caption == "у моря" and note.tags == ["море", "кружок"]
    assert note.duration_s == 8.0
    assert fakes.storage.files[note.storage_key] == b"ROUND:CLIP"
    assert result.message_ids == [message.id]


async def test_video_note_that_cannot_be_cut_goes_as_a_plain_video(db: AsyncSession,
                                                                   deps: Deps,
                                                                   fakes: Fakes) -> None:
    chat, ctx = await context(db, deps)
    clip = await make_media(db, chat.persona_id, kind=MediaKind.VIDEO, storage=fakes.storage)
    fakes.tools.video_note_error = PortError("ffmpeg exited 1")
    action = VideoNoteAction(type="video_note", media_id=clip.id)
    handler = VideoNoteHandler()

    prepared = await handler.prepare(action, ctx)
    assert prepared == RoundFailed("ffmpeg exited 1")
    result = ApplyResult()
    await handler.apply(db, action, prepared, ctx, result)

    [message] = await messages_of(db, chat)
    assert (message.kind, message.media_id) == (MessageKind.VIDEO, clip.id)
    assert "кружок не получился" in result.notes[0]


async def test_video_note_generate_starts_a_spoken_generation(db: AsyncSession,
                                                              deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    action = VideoNoteAction(type="video_note", generate=NOTE_SPEC)
    handler = VideoNoteHandler()
    assert await handler.prepare(action, ctx) is None
    result = ApplyResult()
    await handler.apply(db, action, None, ctx, result)

    assert await messages_of(db, chat) == []           # no announcing text, only the status
    [generation] = await all_rows(db, select(Generation).where(Generation.chat_id == chat.id))
    assert generation.kind == GenerationKind.VIDEO_NOTE and generation.model == "video-model"
    assert generation.speech == "Дима, привет! Как прошёл день?"
    assert result.generation_ids == [generation.id]


def test_each_handler_names_the_status_of_its_slow_work() -> None:
    def busy(action: BaseModel) -> tuple[str, float] | None:
        status = handler_for(action).busy(action)
        return None if status is None else (status.action.value, status.seconds)

    assert busy(TextAction(type="text", text="a")) is None
    assert busy(VoiceAction(type="voice", text="a")) == ("record_audio", 30.0)
    assert busy(PhotoAction(type="photo", media_id=1)) is None
    assert busy(PhotoAction(type="photo", generate=SPEC)) == ("upload_photo", 120.0)
    assert busy(VideoAction(type="video", generate=SPEC)) == ("upload_video", 400.0)
    assert busy(VideoNoteAction(type="video_note", generate=NOTE_SPEC)) == ("record_round",
                                                                            400.0)
    assert busy(VideoNoteAction(type="video_note", media_id=1)) is None
