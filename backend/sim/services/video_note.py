"""AI video notes ("кружочки") of a persona, sent to ONE allowlisted account only.

The owner asks for a clip of a persona; the worker job takes it from the cache or has the
Broker generate it, turns it into a Telegram video note and queues the message in the
allowlisted dialog.

Rules kept here and re-checked by the SEND job (`OutboundService.ai_video_gate`):
- allowlist: the owner setting `video_note_owner_telegram_id`; 0 (the default) = nobody, and
  nothing is generated or sent. Only the private dialog of a Telegram userbot channel with
  exactly that account qualifies;
- no "recording a video note" chat status is shown: the clip is generated, not recorded;
- cache: one file per (persona, prompt, duration, resolution, aspect ratio, reference image,
  encoder version). The same request reuses the file; only a new request is generated (and
  billed). The file name is the hash, so the cache needs no table."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Channel, Dialog, Message, Persona, PersonaMedia
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.media import MediaStore
from sim.clock import Clock, utcnow
from sim.domain.enums import ChannelKind, MediaKind
from sim.ports.video import VideoError, VideoPort, VideoResult
from sim.services import jobkinds
from sim.services.ai_media import (
    AI_VIDEO_PREFIX,
    MEDIA_URL,
    ai_message,
    failed_message,
    stored_name,
)
from sim.services.app_settings import AppSettingsService
from sim.services.dialog_state import apply_message

log = logging.getLogger(__name__)

OWNER_SETTING = "video_note_owner_telegram_id"
VIDEO_NOTE_WORKFLOW = "video-note"
NOTE_FAILED = "[кружочек ИИ не создан]"
ASPECT_RATIO = "9:16"                # portrait: the face stays inside the centre crop
ENCODER_VERSION = 1                  # bump when sim.adapters.video changes the output
RESOLUTIONS = ("360p", "720p")
MIN_DURATION_S, MAX_DURATION_S = 3, 15
MAX_PROMPT_CHARS = 4000
MAX_REFERENCE_BYTES = 4 * 1024 * 1024
REFERENCE_MIMES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                   "webp": "image/webp"}


class VideoNoteRefused(Exception):
    """The request cannot be served as asked (no allowlist, no dialog with the owner account,
    unknown persona or reference). Nothing was generated or sent."""


class VideoNoteEncoderPort(Protocol):
    async def to_video_note(self, video: bytes) -> bytes: ...


@dataclass(frozen=True)
class VideoNoteRequest:
    persona_id: int
    prompt: str
    duration_s: int = 5
    resolution: str = "360p"
    reference_media_id: int | None = None

    def payload(self) -> dict[str, Any]:
        return {"persona_id": self.persona_id, "prompt": self.prompt,
                "duration_s": self.duration_s, "resolution": self.resolution,
                "reference_media_id": self.reference_media_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> VideoNoteRequest:
        reference = payload.get("reference_media_id")
        return cls(int(payload["persona_id"]), str(payload["prompt"]),
                   int(payload["duration_s"]), str(payload["resolution"]),
                   int(reference) if reference is not None else None)


@dataclass(frozen=True)
class Reference:
    data: bytes
    mime: str


@dataclass(frozen=True)
class Plan:
    """A validated request: where the note goes and which cache file it is."""
    dialog: Dialog
    cache_key: str
    reference: Reference | None

    @property
    def name(self) -> str:
        return cache_name(self.cache_key)


def normal_prompt(prompt: str) -> str:
    """Whitespace does not make a new request."""
    return " ".join(prompt.split())


def cache_key(request: VideoNoteRequest, reference: Reference | None) -> str:
    """The hash of everything that changes the clip: persona, prompt and parameters."""
    return hashlib.sha256(json.dumps({
        "persona_id": request.persona_id, "prompt": normal_prompt(request.prompt),
        "duration_s": request.duration_s, "resolution": request.resolution,
        "aspect_ratio": ASPECT_RATIO, "encoder": ENCODER_VERSION,
        "reference_sha256": hashlib.sha256(reference.data).hexdigest() if reference else None,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def cache_name(key: str) -> str:
    return stored_name(AI_VIDEO_PREFIX, key, "mp4")


def owner_allowed(dialog: Dialog, owner_id: int) -> bool:
    """The private dialog of a Telegram userbot channel with exactly the allowlisted account."""
    return (owner_id > 0 and dialog.channel.kind is ChannelKind.TELEGRAM_USER
            and dialog.external_thread_id == str(owner_id))


async def owner_telegram_id(session: AsyncSession) -> int:
    """The allowlisted account from the owner settings; 0 = not set, send to nobody."""
    return int((await AppSettingsService(session).item(OWNER_SETTING)).value)


class OwnerVideoNotes:
    def __init__(
        self, session: AsyncSession, video: VideoPort | None, encoder: VideoNoteEncoderPort,
        media: MediaStore, *, clock: Clock = utcnow,
    ) -> None:
        self.session = session
        self.video = video
        self.encoder = encoder
        self.media = media
        self.clock = clock
        self.jobs = JobRepo(session)

    # ---- request (API) -------------------------------------------------------------------
    async def request(self, request: VideoNoteRequest) -> dict[str, Any]:
        """Validate and enqueue; the worker generates and sends. Raises VideoNoteRefused."""
        plan = await self.plan(request)
        dedup = jobkinds.video_note_key(plan.dialog.id, plan.cache_key[:40])
        cached = self.media.path(plan.name) is not None
        if await self.jobs.pending_by_key(dedup) is None:
            await self.jobs.enqueue(jobkinds.VIDEO_NOTE, request.payload(), self.clock(),
                                    dedup_key=dedup, max_attempts=1)
        return {"status": "queued", "cached": cached, "cache_key": plan.cache_key,
                "dialog_id": plan.dialog.id}

    async def plan(self, request: VideoNoteRequest) -> Plan:
        owner_id = await owner_telegram_id(self.session)
        if owner_id <= 0:
            raise VideoNoteRefused(
                "Получатель не задан: укажите Telegram id в настройке «Кружочки ИИ». "
                "Пока он не задан, кружочки не создаются и не отправляются.")
        if await self.session.get(Persona, request.persona_id) is None:
            raise VideoNoteRefused("Персона не найдена")
        dialog = await self.owner_dialog(request.persona_id, owner_id)
        if dialog is None:
            raise VideoNoteRefused(
                "Нет личного диалога персоны с разрешённым аккаунтом в Telegram: "
                "сначала напишите ей с этого аккаунта.")
        reference = await self.reference(request)
        return Plan(dialog, cache_key(request, reference), reference)

    async def owner_dialog(self, persona_id: int, owner_id: int) -> Dialog | None:
        rows = (await self.session.scalars(
            select(Dialog).join(Channel, Dialog.channel_id == Channel.id)
            .where(Channel.persona_id == persona_id,
                   Channel.kind == ChannelKind.TELEGRAM_USER,
                   Channel.is_archived.is_(False),
                   Dialog.external_thread_id == str(owner_id))
            .order_by(Dialog.id)
        )).unique().all()
        return next((d for d in rows if owner_allowed(d, owner_id)), None)

    async def reference(self, request: VideoNoteRequest) -> Reference | None:
        """The optional reference photo from the persona gallery (how she looks)."""
        if request.reference_media_id is None:
            return None
        row = await self.session.get(PersonaMedia, request.reference_media_id)
        if row is None or row.persona_id != request.persona_id or row.kind is not MediaKind.PHOTO:
            raise VideoNoteRefused("Референс должен быть фото из галереи этой персоны")
        mime = REFERENCE_MIMES.get(row.storage_name.rsplit(".", 1)[-1].lower())
        path = self.media.path(row.storage_name)
        if mime is None or path is None:
            raise VideoNoteRefused("Файл референса недоступен или не JPEG/PNG/WebP")
        data = path.read_bytes()
        if not data or len(data) > MAX_REFERENCE_BYTES:
            raise VideoNoteRefused("Референс должен быть не больше 4 МБ")
        return Reference(data, mime)

    # ---- job (worker) --------------------------------------------------------------------
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Cache hit: reuse the file. Miss: generate, encode, store. Then queue label + note.

        A refusal or a generation failure ends the job without a retry (a clip is billed): the
        reason is kept as a failed, never-sent message in the owner dialog."""
        request = VideoNoteRequest.from_payload(payload)
        try:
            plan = await self.plan(request)    # the allowlist is re-read at run time
        except VideoNoteRefused as exc:
            log.warning("video note not made: %s", exc)
            return {"outcome": "refused", "error": str(exc)[:300]}
        cached = self.media.path(plan.name) is not None
        cost: float | None = None
        model: str | None = None
        if not cached:
            try:
                clip = await self.generate(request, plan)
                await self.media.save_as(plan.name, await self.encoder.to_video_note(clip.video))
            except VideoError as exc:
                self.report_failure(plan.dialog, str(exc))
                return {"outcome": "failed", "error": str(exc)[:300]}
            cost, model = clip.cost_usd, clip.model
        note = await self.queue(plan, cost=cost, model=model)
        return {"outcome": "queued", "cached": cached, "message_id": note.id, "cost_usd": cost}

    async def generate(self, request: VideoNoteRequest, plan: Plan) -> VideoResult:
        if self.video is None:
            raise VideoError("video generation is not configured")
        return await self.video.generate_video(
            normal_prompt(request.prompt), duration_s=request.duration_s,
            resolution=request.resolution, aspect_ratio=ASPECT_RATIO,
            image=plan.reference.data if plan.reference else None,
            image_mime=plan.reference.mime if plan.reference else None,
            workflow=VIDEO_NOTE_WORKFLOW,
        )

    async def queue(self, plan: Plan, *, cost: float | None, model: str | None) -> Message:
        """One AI message in the owner dialog: the video note."""
        now = self.clock()
        note = self.message(plan.dialog, "", media_kind=MediaKind.VIDEO_NOTE.value,
                            media_url=MEDIA_URL + plan.name, cost_usd=cost,
                            model=model[:120] if model else None)
        self.session.add(note)
        await self.session.flush()
        apply_message(plan.dialog, note)
        await self.jobs.enqueue(
            jobkinds.SEND, {"message_id": note.id, "final": True},
            now, dedup_key=jobkinds.send_key(note.id),
        )
        return note

    def message(self, dialog: Dialog, text: str, **fields: Any) -> Message:
        return ai_message(dialog, text, self.clock(), **fields)

    def report_failure(self, dialog: Dialog, error: str) -> None:
        """Why no note came: a failed, never-sent message in the owner dialog."""
        self.session.add(failed_message(dialog, NOTE_FAILED, error, self.clock()))
