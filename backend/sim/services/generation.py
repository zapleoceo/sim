"""Photo / video the persona asked for (docs/reply.md, «Генерация»).

`start_generation` (inside the reply turn) creates the `generation` row with the persona's
appearance references and writes `generation_started`; the `agent.run_generation` task then
calls `GenerationService.run`:
- photo: image model with the references -> media library (generated, caption, tags) -> send;
- video: first a photo by the references (also kept in the library), then the video model
  animates that photo -> media library -> send.
A failure marks the generation failed and writes `generation_failed`; the client gets nothing."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Generation, MediaAsset, Persona
from sim.db.session import transaction
from sim.domain.enums import (
    Author,
    EventKind,
    GenerationKind,
    GenerationStatus,
    LlmPurpose,
    MediaKind,
    MediaSource,
    MessageKind,
)
from sim.domain.plan import GenerateSpec
from sim.events import append_event
from sim.ports import MediaResult, PortError
from sim.services.deps import Deps
from sim.services.llm_log import logged_call
from sim.services.media_library import add_media, normalize_tags
from sim.services.outbox import enqueue_message

log = logging.getLogger(__name__)

MAX_REFERENCES = 8
FILE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                   "video/mp4": "mp4"}


async def reference_ids(session: AsyncSession, persona_id: int) -> list[int]:
    """The persona's appearance references: library photos flagged `is_reference`."""
    rows = await session.execute(
        select(MediaAsset.id)
        .where(MediaAsset.persona_id == persona_id, MediaAsset.source == MediaSource.LIBRARY,
               MediaAsset.kind == MediaKind.PHOTO, MediaAsset.is_reference.is_(True))
        .order_by(MediaAsset.id)
        .limit(MAX_REFERENCES)
    )
    return list(rows.scalars())


async def start_generation(session: AsyncSession, *, persona: Persona, chat_id: int | None,
                           reply_run_id: int | None, kind: GenerationKind,
                           spec: GenerateSpec) -> Generation:
    model = persona.model_video if kind == GenerationKind.VIDEO else persona.model_image
    generation = Generation(
        persona_id=persona.id, chat_id=chat_id, reply_run_id=reply_run_id, kind=kind,
        prompt=spec.prompt, caption=spec.caption.strip(), tags=normalize_tags(spec.tags),
        ref_media_ids=await reference_ids(session, persona.id),
        status=GenerationStatus.QUEUED, model=model,
    )
    session.add(generation)
    await session.flush()
    if chat_id is not None:
        await append_event(
            session, chat_id=chat_id, kind=EventKind.GENERATION_STARTED, actor="persona",
            generation_id=generation.id, reply_run_id=reply_run_id,
            data={"kind": kind.value, "caption": generation.caption},
        )
    return generation


@dataclass(frozen=True)
class _Job:
    """What the slow part needs, read before any model call."""

    id: int
    kind: GenerationKind
    persona_id: int
    chat_id: int | None
    reply_run_id: int | None
    prompt: str
    caption: str
    tags: list[str]
    model_image: str
    model_video: str
    references: list[tuple[bytes, str]]


def _file_name(prefix: str, generation_id: int, mime: str) -> str:
    return f"{prefix}-{generation_id}.{FILE_EXTENSIONS.get(mime, 'bin')}"


class GenerationService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def run(self, generation_id: int) -> GenerationStatus | None:
        job = await self._claim(generation_id)
        if job is None:
            return None
        cost = 0.0
        try:
            photo = await self._image(job)
            cost += photo.usage.cost_usd or 0.0
            async with transaction(self.deps.sessions) as session:
                photo_asset = await self._store(session, job, photo, MediaKind.PHOTO)
                photo_id = photo_asset.id
            if job.kind == GenerationKind.VIDEO:
                video = await self._video(job, photo)
                cost += video.usage.cost_usd or 0.0
                final, final_kind, broker_job = video, MediaKind.VIDEO, video.usage.job_id
            else:
                final, final_kind, broker_job = photo, MediaKind.PHOTO, photo.usage.job_id
        except PortError as exc:
            await self._fail(job, str(exc) or type(exc).__name__, cost)
            return GenerationStatus.FAILED
        message_id = await self._finish(job, final, final_kind, photo_id, broker_job, cost)
        if message_id is not None and job.chat_id is not None:
            await self.deps.tasks.send(message_id, job.chat_id)
        return GenerationStatus.DONE

    async def _claim(self, generation_id: int) -> _Job | None:
        """QUEUED -> RUNNING (idempotent: a retried task does not generate twice)."""
        async with transaction(self.deps.sessions) as session:
            generation = await session.get(Generation, generation_id, with_for_update=True)
            if generation is None or generation.status != GenerationStatus.QUEUED:
                return None
            persona = await session.get(Persona, generation.persona_id)
            if persona is None:
                return None
            generation.status = GenerationStatus.RUNNING
            refs = []
            for media_id in generation.ref_media_ids or []:
                asset = await session.get(MediaAsset, media_id)
                if asset is None:
                    continue
                data = await self.deps.storage.get(asset.storage_key)
                refs.append(await self.deps.tools.image_for_model(data, asset.mime))
            return _Job(
                id=generation.id, kind=generation.kind, persona_id=generation.persona_id,
                chat_id=generation.chat_id, reply_run_id=generation.reply_run_id,
                prompt=generation.prompt, caption=generation.caption,
                tags=list(generation.tags or []), model_image=persona.model_image,
                model_video=persona.model_video, references=refs,
            )

    async def _image(self, job: _Job) -> MediaResult:
        return await logged_call(
            self.deps.sessions, purpose=LlmPurpose.IMAGE, model=job.model_image,
            request={"prompt": job.prompt, "references": len(job.references),
                     "generation_id": job.id},
            call=lambda: self.deps.image_gen.generate_image(
                prompt=job.prompt, references=job.references, model=job.model_image),
            chat_id=job.chat_id, reply_run_id=job.reply_run_id,
        )

    async def _video(self, job: _Job, first_frame: MediaResult) -> MediaResult:
        return await logged_call(
            self.deps.sessions, purpose=LlmPurpose.VIDEO, model=job.model_video,
            request={"prompt": job.prompt, "first_frame": first_frame.mime,
                     "generation_id": job.id},
            call=lambda: self.deps.video_gen.generate_video(
                prompt=job.prompt, first_frame=(first_frame.data, first_frame.mime),
                model=job.model_video),
            chat_id=job.chat_id, reply_run_id=job.reply_run_id,
        )

    async def _store(self, session: AsyncSession, job: _Job, result: MediaResult,
                     kind: MediaKind) -> MediaAsset:
        return await add_media(
            session, self.deps.storage, self.deps.tools, persona_id=job.persona_id,
            data=result.data, mime=result.mime,
            file_name=_file_name(kind.value, job.id, result.mime),
            source=MediaSource.GENERATED, kind=kind, caption=job.caption, tags=job.tags,
            generation_id=job.id,
        )

    async def _finish(self, job: _Job, final: MediaResult, kind: MediaKind, photo_id: int,
                      broker_job: str | None, cost: float) -> int | None:
        async with transaction(self.deps.sessions) as session:
            generation = await session.get(Generation, job.id)
            if generation is None:
                return None
            result_id = photo_id
            if kind == MediaKind.VIDEO:
                result_id = (await self._store(session, job, final, MediaKind.VIDEO)).id
            generation.status = GenerationStatus.DONE
            generation.result_media_id = result_id
            generation.broker_job_id = broker_job
            generation.cost_usd = cost or None
            generation.finished_at = self.deps.clock.now()
            if job.chat_id is None or await session.get(Chat, job.chat_id) is None:
                return None
            message = await enqueue_message(
                session, chat_id=job.chat_id, author=Author.PERSONA,
                kind=MessageKind.VIDEO if kind == MediaKind.VIDEO else MessageKind.PHOTO,
                media_id=result_id, reply_run_id=job.reply_run_id,
            )
            return message.id

    async def _fail(self, job: _Job, error: str, cost: float) -> None:
        log.warning("generation %s failed: %s", job.id, error)
        async with transaction(self.deps.sessions) as session:
            generation = await session.get(Generation, job.id)
            if generation is None:
                return
            generation.status = GenerationStatus.FAILED
            generation.error = error
            generation.cost_usd = cost or None
            generation.finished_at = self.deps.clock.now()
            if job.chat_id is not None:
                await append_event(
                    session, chat_id=job.chat_id, kind=EventKind.GENERATION_FAILED,
                    actor="system", generation_id=job.id, reply_run_id=job.reply_run_id,
                    data={"kind": job.kind.value, "caption": job.caption, "error": error[:300]},
                )
