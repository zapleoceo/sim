"""Photo / video / video note the persona asked for (docs/reply.md, «Генерация»).

`start_generation` (inside the reply turn) creates the `generation` row with the persona's
appearance references and writes `generation_started`; the `agent.run_generation` task then
calls `GenerationService.run`. When it starts, the scene of this moment is fixed on the row
(`generation.scene`, sim/services/life/scene.py) and the prompt is built by ONE function
(`sim.services.visual.media_prompt`): appearance + scene + shot type + the model's pose line;
the latest photos of the same event are added as continuity references, her constant details
(`persona.life.look`) go into every prompt, and a picture at home names the room of her one
apartment with that room's earlier photos as references (docs/persona-life.md, «Облик и дом»).
- photo: image model with the references -> media library (generated, caption, tags) -> send;
- video: first a photo by the references (also kept in the library), then the video model
  animates that photo (selfie framing and her exact words when she speaks, the sound of the
  scene) -> media library -> send;
- video note: like a video (always a spoken selfie, at most `VIDEO_NOTE_GEN_S`), then
  `tools.to_video_note` cuts it to a 640x640 circle -> media library (video) -> `video_note`.
While it runs the client sees «отправляет фото / видео», «записывает кружок»; a failure (a Broker
error, or any other error up to storing the result) marks the generation failed, writes
`generation_failed` and clears that status; the client gets nothing. The task runs in its own
queue `media` (sim/tasks/queue.py); a generation whose worker died is failed by the reaper
(sim/services/reaper.py) after its budget (sim/services/generation_state.py)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import Select, select
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
from sim.services.generation_state import IN_PROGRESS
from sim.services.life.home import home_room
from sim.services.life.store import load_life
from sim.services.llm_log import logged_call
from sim.services.media_library import add_media, normalize_tags
from sim.services.outbox import enqueue_message
from sim.services.presence import show_busy, stop_busy
from sim.services.queue import after_commit
from sim.services.visual import (
    APPEARANCE_REFS,
    CONTINUITY_REFS,
    ROOM_REFS,
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
    """The latest generated photos of the same day in the SAME OUTFIT, oldest first — clothes
    stay the same across events until the plan changes them (another place still shares the
    clothes; `same_place` tells the prompt whether the room is the same too)."""
    rows = await session.scalars(
        select(MediaAsset.id)
        .join(Generation, Generation.id == MediaAsset.generation_id)
        .where(Generation.persona_id == persona_id,
               Generation.status == GenerationStatus.DONE,
               MediaAsset.kind == MediaKind.PHOTO,
               Generation.scene["day"].astext == scene.day.isoformat(),
               Generation.scene["outfit"].astext == scene.outfit)
        .order_by(MediaAsset.id.desc())
        .limit(CONTINUITY_REFS))
    return list(reversed(list(rows)))


async def same_place(session: AsyncSession, media_ids: list[int], scene: SceneCard,
                     room: str | None = None) -> bool:
    """True when every continuity photo was taken in the scene's place (at home: in the same
    room of her apartment)."""
    if not media_ids:
        return False
    key, value = ("room", room) if room is not None else ("place", scene.place)
    places = set(await session.scalars(
        select(Generation.scene[key].astext)
        .join(MediaAsset, MediaAsset.generation_id == Generation.id)
        .where(MediaAsset.id.in_(media_ids))))
    return places == {value}


def _room_photo(persona_id: int, room: str, skip: list[int], *,
                latest: bool) -> Select[tuple[int]]:
    return (select(MediaAsset.id)
            .join(Generation, Generation.id == MediaAsset.generation_id)
            .where(Generation.persona_id == persona_id,
                   Generation.status == GenerationStatus.DONE,
                   MediaAsset.kind == MediaKind.PHOTO,
                   Generation.scene["room"].astext == room,
                   MediaAsset.id.not_in(skip or [0]))
            .order_by(MediaAsset.id.desc() if latest else MediaAsset.id)
            .limit(1))


async def room_ids(session: AsyncSession, persona_id: int, room: str,
                   skip: list[int]) -> list[int]:
    """Earlier generated photos of this room of her apartment: the FIRST one (it fixed how the
    room looks) and the latest one, not already among `skip`."""
    found = [await session.scalar(_room_photo(persona_id, room, skip, latest=latest))
             for latest in (False, True)]
    return list(dict.fromkeys(i for i in found if i is not None))[:ROOM_REFS]


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


@dataclass
class _Spent:
    """The cost of the model calls so far, kept when a later step fails."""

    usd: float = 0.0


def _file_name(prefix: str, generation_id: int, mime: str) -> str:
    return f"{prefix}-{generation_id}.{FILE_EXTENSIONS.get(mime, 'bin')}"


class GenerationService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def run(self, generation_id: int) -> GenerationStatus | None:
        """Claims and makes the generation. A Broker error fails it (status FAILED); any other
        error, storing the result included, fails it too and is raised to the job list —
        a claimed generation never stays `running`."""
        job = await self._claim(generation_id)
        if job is None:
            return None
        spent = _Spent()
        try:
            message_id = await self._make(job, spent)
        except PortError as exc:
            await self._fail(job, str(exc) or type(exc).__name__, spent.usd)
            return GenerationStatus.FAILED
        except Exception as exc:
            await self._fail(job, f"внутренняя ошибка: {exc!r}", spent.usd)
            raise
        if message_id is not None and job.chat_id is not None:
            await after_commit(f"generation {job.id}: send {message_id}",
                               self.deps.tasks.send(message_id, job.chat_id))
        return GenerationStatus.DONE

    async def _make(self, job: _Job, spent: _Spent) -> int | None:
        """The slow part: the media, stored, the generation DONE and its message enqueued (one
        transaction). Returns the message id; `spent` counts the cost as it goes."""
        duration: float | None = None
        await self._busy(job)
        photo = await self._image(job)
        spent.usd += photo.usage.cost_usd or 0.0
        async with transaction(self.deps.sessions) as session:
            photo_asset = await self._store(session, job, photo, MediaKind.PHOTO)
            photo_id = photo_asset.id
        final, final_kind, broker_job = photo, MediaKind.PHOTO, photo.usage.job_id
        if job.kind in VIDEO_KINDS:
            await self._busy(job)           # the clip takes minutes: refresh the status
            video = await self._video(job, photo)
            spent.usd += video.usage.cost_usd or 0.0
            final, final_kind, broker_job = video, MediaKind.VIDEO, video.usage.job_id
        if job.kind == GenerationKind.VIDEO_NOTE:
            note, duration = await self.deps.tools.to_video_note(final.data)
            final = replace(final, data=note, mime=VIDEO_NOTE_MIME)
        return await self._finish(job, final, final_kind, photo_id, broker_job, spent.usd,
                                  duration)

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
            room = home_room(life.life.home, scene, generation.prompt)
            rooms = (await room_ids(session, persona.id, room.name, identity)
                     if room is not None else [])
            continuity = [media_id for media_id in (
                await continuity_ids(session, persona.id, scene) if scene is not None else [])
                if media_id not in rooms]
            here = scene is not None and await same_place(
                session, continuity, scene, room.name if room is not None else None)
            spoken = generation.kind == GenerationKind.VIDEO_NOTE or bool(generation.speech)
            hint = str((generation.scene or {}).get("shot_hint", ""))
            shot = choose_shot(scene, hint, spoken=spoken)
            camera = camera_for(scene)
            if camera.hold == CameraHold.IMPOSSIBLE:     # the scene moved on since the plan
                camera = Camera(CameraHold.PROPPED, f"телефон прислонён ({camera.reason})")
            generation.scene = {**(scene.as_json() if scene is not None else {}),
                                "shot": shot.value, **({"shot_hint": hint} if hint else {}),
                                **({"camera": camera.hold.value, "camera_reason": camera.reason}
                                   if spoken else {}),
                                **({"room": room.name} if room is not None else {})}
            generation.ref_media_ids = identity + continuity + rooms
            refs = await self._references(session, identity + continuity + rooms)
            prompt_args = {"action": generation.prompt, "scene": scene, "shot": shot,
                           "look": life.life.look, "room": room, "home": life.life.home}
            return _Job(
                id=generation.id, kind=generation.kind, persona_id=generation.persona_id,
                chat_id=generation.chat_id, reply_run_id=generation.reply_run_id,
                image_prompt=media_prompt(
                    stage="image", appearance=persona.appearance, identity_refs=len(identity),
                    continuity_refs=len(continuity), continuity_same_place=here,
                    room_refs=len(rooms), **prompt_args),
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
            generation = await session.get(Generation, job.id, with_for_update=True)
            if generation is None:
                return
            if cost:
                generation.cost_usd = cost
            await fail_generation(session, generation, error, self.deps.clock.now())
        if job.chat_id is not None:
            await stop_busy(self.deps.tasks, job.chat_id)


async def fail_generation(session: AsyncSession, generation: Generation, error: str,
                          now: datetime) -> bool:
    """Marks an unfinished generation FAILED and writes `generation_failed` (the operator sees
    it; the client gets nothing). False when it has already finished. The caller stops the
    chat's live status after its commit (`stop_busy`)."""
    if generation.status not in IN_PROGRESS:
        return False
    generation.status = GenerationStatus.FAILED
    generation.error = error
    generation.finished_at = now
    if generation.chat_id is not None:
        await append_event(
            session, chat_id=generation.chat_id, kind=EventKind.GENERATION_FAILED,
            actor="system", generation_id=generation.id, reply_run_id=generation.reply_run_id,
            data={"kind": generation.kind.value, "caption": generation.caption,
                  "error": error[:300]},
        )
    return True
