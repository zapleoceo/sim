"""Photo / video / video note the persona asked for (docs/reply.md, «Генерация»).

`start_generation` (inside the reply turn) creates the `generation` row with the persona's
appearance references and writes `generation_started`; the `agent.run_generation` task then
calls `GenerationService.run`. When it starts, the scene of this moment is fixed on the row
(`generation.scene`, sim/services/life/scene.py) and the prompt is built by ONE function
(`sim.services.visual.media_prompt`): appearance + scene + shot type + the model's pose line;
the latest photos of the same event are added as continuity references (docs/persona-life.md).
- photo: image model with the references -> media library (generated, caption, tags) -> send;
- video: first a photo by the references (also kept in the library), then the video model
  animates that photo (selfie framing and her exact words when she speaks, the sound of the
  scene) -> media library -> send;
- video note: like a video (always a spoken selfie, at most `VIDEO_NOTE_GEN_S`), then
  `tools.to_video_note` cuts it to a 640x640 circle -> media library (video) -> `video_note`.
While it runs the client sees «отправляет фото / видео», «записывает кружок»; a failure marks the
generation failed, writes `generation_failed` and clears that status; the client gets nothing."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Generation, MediaAsset, Persona
from sim.db.session import transaction
from sim.domain.agents import AgentConfig, AgentKey, agent_config
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
from sim.domain.presence import busy_with
from sim.domain.scene import Camera, CameraHold, SceneCard
from sim.events import append_event
from sim.ports import MediaResult, PortError
from sim.services.deps import Deps
from sim.services.life.store import load_life
from sim.services.llm_log import logged_call
from sim.services.media_library import add_media, normalize_tags
from sim.services.outbox import enqueue_message
from sim.services.presence import show_busy, stop_busy
from sim.services.visual import (
    APPEARANCE_REFS,
    CONTINUITY_REFS,
    camera_for,
    choose_shot,
    media_prompt,
)

log = logging.getLogger(__name__)

VIDEO_GEN_S = 5                 # a plain video clip
VIDEO_NOTE_GEN_S = 8            # a round video message: one or two spoken sentences
VIDEO_NOTE_MIME = "video/mp4"
FILE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                   "video/mp4": "mp4"}
# Generations made by the video model (photo first, then the clip).
VIDEO_KINDS = (GenerationKind.VIDEO, GenerationKind.VIDEO_NOTE)
# What the result is sent as.
MESSAGE_KINDS = {GenerationKind.PHOTO: MessageKind.PHOTO, GenerationKind.VIDEO: MessageKind.VIDEO,
                 GenerationKind.VIDEO_NOTE: MessageKind.VIDEO_NOTE}


async def reference_ids(session: AsyncSession, persona_id: int) -> list[int]:
    """The persona's appearance references: library photos flagged `is_reference`."""
    rows = await session.execute(
        select(MediaAsset.id)
        .where(MediaAsset.persona_id == persona_id, MediaAsset.source == MediaSource.LIBRARY,
               MediaAsset.kind == MediaKind.PHOTO, MediaAsset.is_reference.is_(True))
        .order_by(MediaAsset.id)
        .limit(APPEARANCE_REFS)
    )
    return list(rows.scalars())


async def continuity_ids(session: AsyncSession, persona_id: int, scene: SceneCard) -> list[int]:
    """The latest generated photos of the SAME event (same day, same event id), oldest first.
    A new event is a new scene: older photos are never used."""
    rows = await session.scalars(
        select(MediaAsset.id)
        .join(Generation, Generation.id == MediaAsset.generation_id)
        .where(Generation.persona_id == persona_id,
               Generation.status == GenerationStatus.DONE,
               MediaAsset.kind == MediaKind.PHOTO,
               Generation.scene["day"].astext == scene.day.isoformat(),
               Generation.scene["event_id"].astext == scene.event_id)
        .order_by(MediaAsset.id.desc())
        .limit(CONTINUITY_REFS))
    return list(reversed(list(rows)))


async def start_generation(session: AsyncSession, *, persona: Persona, chat_id: int | None,
                           reply_run_id: int | None, kind: GenerationKind,
                           spec: GenerateSpec) -> Generation:
    model = agent_config(persona, AgentKey.VIDEO if kind in VIDEO_KINDS else AgentKey.IMAGE).model
    generation = Generation(
        persona_id=persona.id, chat_id=chat_id, reply_run_id=reply_run_id, kind=kind,
        prompt=spec.prompt, speech=spec.speech.strip() if kind in VIDEO_KINDS else "",
        caption=spec.caption.strip(), tags=normalize_tags(spec.tags),
        ref_media_ids=await reference_ids(session, persona.id),
        scene={"shot_hint": spec.shot} if spec.shot else {},
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
    image_prompt: str
    video_prompt: str
    caption: str
    tags: list[str]
    image_agent: AgentConfig        # «Фотограф»
    video_agent: AgentConfig        # «Видеограф»
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
        await self._busy(job)
        cost = 0.0
        duration: float | None = None
        try:
            photo = await self._image(job)
            cost += photo.usage.cost_usd or 0.0
            async with transaction(self.deps.sessions) as session:
                photo_asset = await self._store(session, job, photo, MediaKind.PHOTO)
                photo_id = photo_asset.id
            final, final_kind, broker_job = photo, MediaKind.PHOTO, photo.usage.job_id
            if job.kind in VIDEO_KINDS:
                await self._busy(job)           # the clip takes minutes: refresh the status
                video = await self._video(job, photo)
                cost += video.usage.cost_usd or 0.0
                final, final_kind, broker_job = video, MediaKind.VIDEO, video.usage.job_id
            if job.kind == GenerationKind.VIDEO_NOTE:
                note, duration = await self.deps.tools.to_video_note(final.data)
                final = replace(final, data=note, mime=VIDEO_NOTE_MIME)
        except PortError as exc:
            await self._fail(job, str(exc) or type(exc).__name__, cost)
            return GenerationStatus.FAILED
        message_id = await self._finish(job, final, final_kind, photo_id, broker_job, cost,
                                        duration)
        if message_id is not None and job.chat_id is not None:
            await self.deps.tasks.send(message_id, job.chat_id)
        return GenerationStatus.DONE

    async def _busy(self, job: _Job) -> None:
        if job.chat_id is not None:
            await show_busy(self.deps.tasks, job.chat_id, busy_with(job.kind.value))

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
            life = await load_life(session, persona, self.deps.clock.now())
            scene = life.scene() if life.has_life else None
            identity = list(generation.ref_media_ids or [])
            continuity = (await continuity_ids(session, persona.id, scene)
                          if scene is not None else [])
            spoken = generation.kind == GenerationKind.VIDEO_NOTE or bool(generation.speech)
            hint = str((generation.scene or {}).get("shot_hint", ""))
            shot = choose_shot(scene, hint, spoken=spoken)
            camera = camera_for(scene)
            if camera.hold == CameraHold.IMPOSSIBLE:     # the scene moved on since the plan
                camera = Camera(CameraHold.PROPPED, f"телефон прислонён ({camera.reason})")
            generation.scene = {**(scene.as_json() if scene is not None else {}),
                                "shot": shot.value, **({"shot_hint": hint} if hint else {}),
                                **({"camera": camera.hold.value, "camera_reason": camera.reason}
                                   if spoken else {})}
            generation.ref_media_ids = identity + continuity
            refs = await self._references(session, identity + continuity)
            prompt_args = {"action": generation.prompt, "scene": scene, "shot": shot}
            return _Job(
                id=generation.id, kind=generation.kind, persona_id=generation.persona_id,
                chat_id=generation.chat_id, reply_run_id=generation.reply_run_id,
                image_prompt=media_prompt(
                    stage="image", appearance=persona.appearance, identity_refs=len(identity),
                    continuity_refs=len(continuity), **prompt_args),
                video_prompt=media_prompt(
                    stage="video", speech=generation.speech or "",
                    language=persona.language or "",
                    round_note=generation.kind == GenerationKind.VIDEO_NOTE, camera=camera,
                    **prompt_args),
                caption=generation.caption, tags=list(generation.tags or []),
                image_agent=agent_config(persona, AgentKey.IMAGE),
                video_agent=agent_config(persona, AgentKey.VIDEO),
                references=refs,
            )

    async def _references(self, session: AsyncSession,
                          media_ids: list[int]) -> list[tuple[bytes, str]]:
        refs = []
        for media_id in media_ids:
            asset = await session.get(MediaAsset, media_id)
            if asset is None:
                continue
            data = await self.deps.storage.get(asset.storage_key)
            refs.append(await self.deps.tools.image_for_model(data, asset.mime))
        return refs

    async def _image(self, job: _Job) -> MediaResult:
        agent = job.image_agent
        return await logged_call(
            self.deps.sessions, purpose=LlmPurpose.IMAGE, model=agent.model,
            request={"prompt": job.image_prompt, "references": len(job.references),
                     "generation_id": job.id},
            call=lambda: self.deps.image_gen.generate_image(
                prompt=job.image_prompt, references=job.references, model=agent.model,
                key_tier=agent.key_tier),
            chat_id=job.chat_id, reply_run_id=job.reply_run_id, key_tier=agent.key_tier,
        )

    async def _video(self, job: _Job, first_frame: MediaResult) -> MediaResult:
        round_note = job.kind == GenerationKind.VIDEO_NOTE
        prompt = job.video_prompt
        duration = VIDEO_NOTE_GEN_S if round_note else VIDEO_GEN_S
        agent = job.video_agent
        return await logged_call(
            self.deps.sessions, purpose=LlmPurpose.VIDEO, model=agent.model,
            request={"prompt": prompt, "first_frame": first_frame.mime, "duration_s": duration,
                     "generation_id": job.id},
            call=lambda: self.deps.video_gen.generate_video(
                prompt=prompt, first_frame=(first_frame.data, first_frame.mime),
                model=agent.model, duration_s=duration, key_tier=agent.key_tier),
            chat_id=job.chat_id, reply_run_id=job.reply_run_id, key_tier=agent.key_tier,
        )

    async def _store(self, session: AsyncSession, job: _Job, result: MediaResult,
                     kind: MediaKind, *, prefix: str | None = None) -> MediaAsset:
        return await add_media(
            session, self.deps.storage, self.deps.tools, persona_id=job.persona_id,
            data=result.data, mime=result.mime,
            file_name=_file_name(prefix or kind.value, job.id, result.mime),
            source=MediaSource.GENERATED, kind=kind, caption=job.caption, tags=job.tags,
            generation_id=job.id,
        )

    async def _finish(self, job: _Job, final: MediaResult, kind: MediaKind, photo_id: int,
                      broker_job: str | None, cost: float, duration: float | None) -> int | None:
        async with transaction(self.deps.sessions) as session:
            generation = await session.get(Generation, job.id)
            if generation is None:
                return None
            result_id = photo_id
            if kind == MediaKind.VIDEO:
                asset = await self._store(session, job, final, MediaKind.VIDEO,
                                          prefix=job.kind.value)
                if asset.duration_s is None:
                    asset.duration_s = duration
                result_id = asset.id
            generation.status = GenerationStatus.DONE
            generation.result_media_id = result_id
            generation.broker_job_id = broker_job
            generation.cost_usd = cost or None
            generation.finished_at = self.deps.clock.now()
            if job.chat_id is None or await session.get(Chat, job.chat_id) is None:
                return None
            message = await enqueue_message(
                session, chat_id=job.chat_id, author=Author.PERSONA,
                kind=MESSAGE_KINDS.get(job.kind, MessageKind.PHOTO), media_id=result_id,
                reply_run_id=job.reply_run_id,
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
        if job.chat_id is not None:
            await stop_busy(self.deps.tasks, job.chat_id)
