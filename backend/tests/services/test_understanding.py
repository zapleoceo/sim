from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import ChatEvent, LlmCall, MediaAsset, Persona
from sim.domain.enums import (
    EventKind,
    LlmPurpose,
    MediaKind,
    MediaSource,
    MessageKind,
    UnderstandingStatus,
)
from sim.ports import PortError
from sim.services.deps import Deps
from sim.services.understanding import NOT_SEEN, VIDEO_FRAMES, Understanding, pick_strategy
from tests.services.conftest import Fakes
from tests.services.factories import add_message, all_rows, fresh, make_chat, make_media


@pytest.mark.parametrize(("message_kind", "kind", "mime", "strategy"), [
    (MessageKind.PHOTO, MediaKind.PHOTO, "image/jpeg", "image"),
    (MessageKind.STICKER, MediaKind.PHOTO, "image/webp", "image"),
    (MessageKind.VIDEO, MediaKind.VIDEO, "video/mp4", "video"),
    (MessageKind.VIDEO_NOTE, MediaKind.VIDEO, "video/mp4", "video_note"),
    (MessageKind.VOICE, MediaKind.AUDIO, "audio/ogg", "transcribe"),
    (MessageKind.FILE, MediaKind.AUDIO, "audio/mpeg", "transcribe"),
    (MessageKind.FILE, MediaKind.FILE, "application/pdf", "pdf"),
    (MessageKind.FILE, MediaKind.FILE, "text/plain", "document"),
])
def test_pick_strategy(message_kind: MessageKind, kind: MediaKind, mime: str,
                       strategy: str) -> None:
    assert pick_strategy(message_kind, kind, mime) == strategy


async def attachment(db: AsyncSession, fakes: Fakes, clock: FixedClock, *,
                     message_kind: MessageKind, kind: MediaKind, mime: str,
                     data: bytes = b"bytes", file_name: str = "",
                     **persona: Any) -> MediaAsset:
    chat = await make_chat(db)
    if persona:
        row = await db.get(Persona, chat.persona_id)
        assert row is not None
        for key, value in persona.items():
            setattr(row, key, value)
    media = await make_media(db, None, kind=kind, source=MediaSource.INCOMING, mime=mime,
                             file_name=file_name, storage=fakes.storage, data=data,
                             understanding=UnderstandingStatus.PENDING)
    await add_message(db, chat, "", at=clock.now(), kind=message_kind, media=media)
    return media


async def understood(db: AsyncSession, media: MediaAsset) -> MediaAsset:
    return await fresh(db, media)


async def purposes(db: AsyncSession) -> list[LlmPurpose]:
    return [c.purpose for c in await all_rows(db, select(LlmCall).order_by(LlmCall.id))]


async def test_photo_is_described_by_the_vision_model(db: AsyncSession, deps: Deps,
                                                      fakes: Fakes, clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.PHOTO,
                             kind=MediaKind.PHOTO, mime="image/png", data=b"PNG")
    fakes.llm.script("vision-model", "Кот спит на диване.")

    assert await Understanding(deps).understand(media.id) == UnderstandingStatus.DONE

    media = await understood(db, media)
    assert media.understanding == UnderstandingStatus.DONE
    assert media.description == "Кот спит на диване."
    [call] = fakes.llm.calls_for("vision-model")
    parts = call["messages"][0].parts
    assert parts[1].data == b"PNG" and parts[1].mime == "image/jpeg"     # via image_for_model
    assert await purposes(db) == [LlmPurpose.VISION]
    [event] = await all_rows(db, select(ChatEvent).where(
        ChatEvent.kind == EventKind.MEDIA_UNDERSTOOD))
    assert event.media_id == media.id and not event.in_context
    # Done once: a second task does nothing.
    assert await Understanding(deps).understand(media.id) is None


async def test_voice_is_transcribed(db: AsyncSession, deps: Deps, fakes: Fakes,
                                    clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VOICE,
                             kind=MediaKind.AUDIO, mime="audio/ogg", data=b"OGG")
    fakes.transcriber.text = "привет, ты где?"

    await Understanding(deps).understand(media.id)

    assert (await understood(db, media)).description == "привет, ты где?"
    assert fakes.transcriber.calls[0]["audio"] == b"OGG"
    assert fakes.transcriber.calls[0]["model"] == "stt-model"
    assert await purposes(db) == [LlmPurpose.TRANSCRIBE]


async def test_video_goes_inline_to_the_vision_model(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes, clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VIDEO,
                             kind=MediaKind.VIDEO, mime="video/mp4", data=b"MP4")
    fakes.llm.script("vision-model", "Человек идёт по пляжу.")

    await Understanding(deps).understand(media.id)

    [call] = fakes.llm.calls_for("vision-model")
    assert call["messages"][0].parts[1].mime == "video/mp4"
    assert (await understood(db, media)).description == "Человек идёт по пляжу."


async def test_a_model_without_video_sees_frames(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                  clock: FixedClock) -> None:
    # 2026-11-22: «Зрение» on Claude refused the video note; frames in order instead.
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VIDEO_NOTE,
                             kind=MediaKind.VIDEO, mime="video/mp4")
    fakes.transcriber.text = "привет"
    fakes.llm.script("vision-model",
                     PortError("HTTP 400: unsupported subscription message block",
                               retryable=False),
                     "Парень машет рукой.")

    await Understanding(deps).understand(media.id)

    inline, framed = fakes.llm.calls_for("vision-model")
    assert inline["messages"][0].parts[1].mime == "video/mp4"
    parts = framed["messages"][0].parts
    assert [part.mime for part in parts[1:]] == ["image/jpeg"] * VIDEO_FRAMES
    assert "кадров видео по порядку" in parts[0].text
    assert ("video_frames", "video/mp4", VIDEO_FRAMES) in fakes.tools.calls
    assert (await understood(db, media)).description == (
        "Видно: Парень машет рукой. Говорит: привет")


async def test_video_note_is_seen_and_heard(db: AsyncSession, deps: Deps, fakes: Fakes,
                                            clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VIDEO_NOTE,
                             kind=MediaKind.VIDEO, mime="video/mp4")
    fakes.transcriber.text = "смотри где я"
    fakes.llm.script("vision-model", "Парень в парке.")

    await Understanding(deps).understand(media.id)

    assert (await understood(db, media)).description == \
        "Видно: Парень в парке. Говорит: смотри где я"


async def test_video_note_prefers_the_transcript_over_the_heard_speech(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VIDEO_NOTE,
                             kind=MediaKind.VIDEO, mime="video/mp4")
    fakes.transcriber.text = "смотри где я"
    fakes.llm.script("vision-model", "Парень в парке. Говорит: смари где я")

    await Understanding(deps).understand(media.id)

    assert (await understood(db, media)).description == \
        "Видно: Парень в парке. Говорит: смотри где я"


async def test_video_note_speech_survives_a_failed_transcription(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VIDEO_NOTE,
                             kind=MediaKind.VIDEO, mime="video/mp4")
    fakes.transcriber.error = PortError("video/mp4 is not audio")
    fakes.llm.script("vision-model", "Парень в парке. Говорит: смотри где я")

    await Understanding(deps).understand(media.id)

    assert (await understood(db, media)).description == \
        "Видно: Парень в парке. Говорит: смотри где я"


async def test_pdf_via_model_with_text_fallback(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.FILE,
                             kind=MediaKind.FILE, mime="application/pdf", file_name="a.pdf")
    fakes.llm.script("vision-model", PortError("pdf is not supported"))
    fakes.tools.document = "Договор аренды  на   год"

    assert await Understanding(deps).understand(media.id) == UnderstandingStatus.DONE

    assert fakes.llm.calls_for("vision-model")[0]["messages"][0].parts[1].mime == \
        "application/pdf"
    assert (await understood(db, media)).description == "Договор аренды на год"
    assert await purposes(db) == [LlmPurpose.DOCUMENT]


async def test_document_text_is_read_by_tools(db: AsyncSession, deps: Deps, fakes: Fakes,
                                              clock: FixedClock) -> None:
    media = await attachment(
        db, fakes, clock, message_kind=MessageKind.FILE, kind=MediaKind.FILE,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_name="cv.docx")
    fakes.tools.document = "Резюме: инженер"

    await Understanding(deps).understand(media.id)

    assert (await understood(db, media)).description == "Резюме: инженер"
    assert fakes.llm.calls == []


async def test_failure_is_recorded(db: AsyncSession, deps: Deps, fakes: Fakes,
                                   clock: FixedClock) -> None:
    media = await attachment(db, fakes, clock, message_kind=MessageKind.PHOTO,
                             kind=MediaKind.PHOTO, mime="image/jpeg", agents={})

    assert await Understanding(deps).understand(media.id) == UnderstandingStatus.FAILED

    media = await understood(db, media)
    assert media.understanding == UnderstandingStatus.FAILED
    assert media.understanding_error == "не выбрана модель распознавания"
    [event] = await all_rows(db, select(ChatEvent).where(
        ChatEvent.kind == EventKind.MEDIA_UNDERSTOOD))
    assert event.data["status"] == "failed"


async def test_owner_vision_prompt_and_key_tier_are_used(db: AsyncSession, deps: Deps,
                                                         fakes: Fakes, clock: FixedClock) -> None:
    """«Зрение» takes the owner's [Фото] section and passes its key tier to the Broker and into
    the log."""
    agents = {"vision": {"model": "vision-model", "key_tier": "free",
                         "prompt": "[Фото]\nОпиши одним словом."}}
    photo = await attachment(db, fakes, clock, message_kind=MessageKind.PHOTO,
                             kind=MediaKind.PHOTO, mime="image/png", agents=agents)
    fakes.llm.script("vision-model", "Кот.")
    await Understanding(deps).understand(photo.id)
    [call] = fakes.llm.calls_for("vision-model")
    assert call["messages"][0].parts[0].text == "Опиши одним словом."
    assert (call["key_tier"], call["max_tokens"]) == ("free", 800)
    [log] = await all_rows(db, select(LlmCall))
    assert log.request["key_tier"] == "free"


async def test_hearing_runs_on_its_default_paid_tier(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes, clock: FixedClock) -> None:
    voice = await attachment(db, fakes, clock, message_kind=MessageKind.VOICE,
                             kind=MediaKind.AUDIO, mime="audio/ogg", data=b"OGG")
    await Understanding(deps).understand(voice.id)
    [call] = fakes.transcriber.calls
    assert (call["model"], call["key_tier"]) == ("stt-model", "paid")


async def test_heard_but_not_seen_says_so(db: AsyncSession, deps: Deps, fakes: Fakes,
                                          clock: FixedClock) -> None:
    # 2026-11-22: vision failed, only the speech came through, and she «described» the
    # picture from an earlier photo. Now the description says the picture was not seen.
    media = await attachment(db, fakes, clock, message_kind=MessageKind.VIDEO_NOTE,
                             kind=MediaKind.VIDEO, mime="video/mp4")
    fakes.transcriber.text = "опиши меня"
    fakes.llm.script("vision-model", PortError("at most 5 attachments"),
                     PortError("at most 5 attachments"))

    await Understanding(deps).understand(media.id)

    assert (await understood(db, media)).description == (
        f"Видно: {NOT_SEEN} Говорит: опиши меня")
    assert VIDEO_FRAMES <= 5                # the Broker's subscription models' limit
