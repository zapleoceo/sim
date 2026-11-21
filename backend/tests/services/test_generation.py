from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ChatEvent, Generation, MediaAsset, Message, Persona
from sim.domain.enums import (
    EventKind,
    GenerationKind,
    GenerationStatus,
    MediaKind,
    MediaSource,
    MessageKind,
)
from sim.domain.plan import GenerateSpec
from sim.ports import PortError
from sim.services.deps import Deps
from sim.services.generation import GenerationService, start_generation
from tests.services.conftest import Fakes
from tests.services.factories import all_rows, fresh, make_chat, make_media

SPEC = GenerateSpec(prompt="селфи у окна утром", caption="утро у окна", tags=["утро"])


async def queued(db: AsyncSession, fakes: Fakes, kind: GenerationKind) -> tuple[Chat, Generation]:
    chat = await make_chat(db)
    persona = await db.get(Persona, chat.persona_id)
    assert persona is not None
    await make_media(db, persona.id, is_reference=True, storage=fakes.storage, data=b"FACE")
    generation = await start_generation(db, persona=persona, chat_id=chat.id, reply_run_id=None,
                                        kind=kind, spec=SPEC)
    return chat, generation


async def generated_media(db: AsyncSession, generation: Generation) -> list[MediaAsset]:
    return await all_rows(db, select(MediaAsset).where(MediaAsset.generation_id == generation.id)
                          .order_by(MediaAsset.id))


async def test_photo_by_references_is_saved_and_sent(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes) -> None:
    chat, generation = await queued(db, fakes, GenerationKind.PHOTO)

    assert await GenerationService(deps).run(generation.id) == GenerationStatus.DONE

    assert fakes.image_gen.calls[0]["references"] == [(b"FACE", "image/jpeg")]
    [photo] = await generated_media(db, generation)
    assert (photo.kind, photo.source, photo.caption, photo.tags) == (
        MediaKind.PHOTO, MediaSource.GENERATED, "утро у окна", ["утро"])
    generation = await fresh(db, generation)
    assert generation.status == GenerationStatus.DONE
    assert generation.result_media_id == photo.id and generation.cost_usd == 0.04
    [message] = await all_rows(db, select(Message).where(Message.chat_id == chat.id))
    assert (message.kind, message.media_id) == (MessageKind.PHOTO, photo.id)
    assert fakes.tasks.of("send") == [("send", message.id, chat.id)]
    # A retried task does not generate twice.
    assert await GenerationService(deps).run(generation.id) is None
    assert len(fakes.image_gen.calls) == 1


async def test_video_is_made_from_a_photo_by_references(db: AsyncSession, deps: Deps,
                                                        fakes: Fakes) -> None:
    chat, generation = await queued(db, fakes, GenerationKind.VIDEO)

    assert await GenerationService(deps).run(generation.id) == GenerationStatus.DONE

    photo, video = await generated_media(db, generation)
    assert photo.kind == MediaKind.PHOTO and video.kind == MediaKind.VIDEO
    first_frame = fakes.video_gen.calls[0]["first_frame"]
    assert first_frame == (fakes.storage.files[photo.storage_key], "image/png")
    assert fakes.video_gen.calls[0]["model"] == "video-model"
    generation = await fresh(db, generation)
    assert generation.result_media_id == video.id
    assert generation.cost_usd is not None and abs(generation.cost_usd - 0.54) < 1e-9
    [message] = await all_rows(db, select(Message).where(Message.chat_id == chat.id))
    assert (message.kind, message.media_id) == (MessageKind.VIDEO, video.id)


async def test_failure_is_shown_to_the_operator_only(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes) -> None:
    chat, generation = await queued(db, fakes, GenerationKind.VIDEO)
    fakes.video_gen.error = PortError("video model down")

    assert await GenerationService(deps).run(generation.id) == GenerationStatus.FAILED

    generation = await fresh(db, generation)
    assert generation.status == GenerationStatus.FAILED
    assert generation.error == "video model down"
    assert await all_rows(db, select(Message).where(Message.chat_id == chat.id)) == []
    assert fakes.tasks.of("send") == []
    [failed] = await all_rows(db, select(ChatEvent).where(
        ChatEvent.chat_id == chat.id, ChatEvent.kind == EventKind.GENERATION_FAILED))
    assert failed.generation_id == generation.id and failed.data["caption"] == "утро у окна"
