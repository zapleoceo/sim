"""The persona's own tool for photos and video notes: during a chat she decides that a photo or
a video note fits, and the system generates it from her gallery (reference photos) and her life
at this moment.

How it works:
- `offer()` says what she may attach to this reply. The answer goes into the reply's dynamic
  context as one block (`Offer.brief()`): either how to ask (a `media` field in her JSON answer),
  or that she cannot send media now and must not promise it;
- she asks with `{"kind": "photo" | "video_note", "scene": "..."}` (`prompt.MediaAsk`); when she
  is asked for media and will not send it (now), she says so with `"decision": "decline" |
  "later"` and her reason (owner setting `media_report`);
- `decide()` writes her decision into the dialog (`sim.services.media_decisions`: what was
  asked, what she decided, her reason, the outcome), re-checks the offer and queues the
  `persona_media` job after her text. `context()` gives the next replies her latest decisions;
- `run()` (worker) builds the prompt: her scene plus her life right now, computed by code from
  the planned part of the local day (`shot_prompt`), takes reference photos from the gallery and
  has the Broker generate the photo (`ImagePort`) or the video note (`OwnerVideoNotes`: the same
  cache, encoder and allowlist as the owner's own notes).

Rules that do not change:
- a video note goes only to the account from the setting «Telegram id для кружочков ИИ»;
- her text never promises the media: it is a separate message that may not come. When the
  generation fails, the reason is a failed, never-sent message in the dialog and the result of
  the job in the system log.

What the offer needs. A photo: the setting «Модель для фото» (`persona_photo_model`), at least
one usable photo in her gallery, a channel that can send photos. A video note: the dialog with
the allowlisted account and a channel that can send notes. Both: no generated media in this
dialog within «Пауза между медиа» (`persona_media_gap_min`); an owner account
(`owner_accounts`) has no such pause, only one generation at a time."""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.audio import AudioError
from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Dialog, Message, Persona, PersonaMedia
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.repositories.persona_media import PersonaMediaRepo
from sim.adapters.media import MediaStore
from sim.adapters.video_process import run_video_process
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus, Delivery, Direction, MediaKind
from sim.ports.image import ImageError, ImagePort, ImageReference
from sim.services import instructions, jobkinds
from sim.services.ai_media import (
    AI_PHOTO_PREFIX,
    AI_VIDEO_PREFIX,
    MEDIA_URL,
    ai_message,
    failed_message,
    stored_name,
)
from sim.services.app_settings import AppSettings, AppSettingsService
from sim.services.dialog_state import apply_message
from sim.services.media_decisions import (
    DECLINED,
    ENDED,
    NOTHING,
    POSTPONED,
    QUEUED,
    REPLY_MAX,
    SILENT,
    UNAVAILABLE,
    UNDECIDED,
    UNSENT,
    MediaDecision,
    MediaDecisionLog,
    load,
    reason_warning,
)
from sim.services.owner_accounts import is_owner_account
from sim.services.persona_clock import age_on, day_part, local_time
from sim.services.persona_media import MediaRejected, checked_suffix
from sim.services.prompt import MEDIA_LATER, Decision, MediaAsk, one_line
from sim.services.video_note import (
    MAX_REFERENCE_BYTES,
    NOTE_FAILED,
    REFERENCE_MIMES,
    ChatVideoTarget,
    OwnerVideoNotes,
    Reference,
    VideoNoteRequest,
    owner_allowed,
)

log = logging.getLogger(__name__)

PHOTO_WORKFLOW = "persona-photo"
PHOTO_FAILED = "[фото ИИ не создано]"
MAX_REFERENCES = 3               # the Broker takes at most 5 images and 10 MB per request
SEND_AFTER_TEXT_S = 2.0          # the media follows her last text message
LEGACY_SCENE_LIGHT = "нейтральное"
NOTE_DURATION_S = 5
NOTE_RESOLUTION = "360p"
TAG_STEM_MIN = 4                 # letters of a gallery tag compared with the scene

KIND_NAMES = {MediaKind.PHOTO: "своё фото", MediaKind.VIDEO_NOTE: "свой кружочек (короткое видео)"}
FRAMES = {MediaKind.PHOTO: "photo_frame", MediaKind.VIDEO_NOTE: "video_note_frame"}  # setting keys
WHO = {"female": "женщина", "male": "мужчина"}


@dataclass(frozen=True)
class Offer:
    """What the persona may attach to this reply."""

    kinds: tuple[MediaKind, ...] = ()

    def brief(self) -> str:
        """The block of the reply's dynamic context."""
        if not self.kinds:
            return instructions.text("media_none")
        return instructions.fill(
            "media_brief",
            kinds=" или ".join(KIND_NAMES[kind] for kind in self.kinds),
            values=" или ".join(f'"{kind.value}"' for kind in self.kinds),
        )


@dataclass(frozen=True)
class ShotEnvironment:
    """Server clock snapshot for synthetic lighting, with no real location or activity."""
    at: datetime
    light: str

    @classmethod
    def capture(cls, timezone: str, now: datetime) -> ShotEnvironment:
        utc = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return cls(utc, day_part(local_time(timezone, utc).hour))

    def payload(self) -> dict[str, str]:
        return {"at": self.at.isoformat(), "light": self.light}

    @staticmethod
    def utc_stamp(stamp: object) -> datetime | None:
        if not isinstance(stamp, str) or not 25 <= len(stamp) <= 32 or not stamp.endswith("+00:00"):
            return None
        try:
            at = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        return at if at.isoformat() == stamp else None

    @classmethod
    def parse(cls, value: object) -> ShotEnvironment | None:
        if not isinstance(value, dict) or set(value) != {"at", "light"}:
            return None
        at, light = cls.utc_stamp(value["at"]), value["light"]
        if at is None or light not in ("ночь", "утро", "день", "вечер"):
            return None
        return cls(at, light)


def who_is(persona: Persona, now_utc: datetime) -> str:
    who = WHO.get(persona.gender, "человек")
    if persona.birth_date is None:
        return who
    return f"{who}, возраст {age_on(persona.birth_date, local_time(persona.timezone, now_utc).date())}"


def shot_prompt(persona: Persona, ask: MediaAsk, environment: ShotEnvironment) -> str:
    """What the image or video model gets: the kind of shot, who is in it (the references show
    how she looks), the scene she asked for and the planned synthetic lighting.
    The exact timestamp is metadata, not part of the provider prompt or its cache key."""
    return "\n".join([
        instructions.text(FRAMES[ask.kind]),
        instructions.fill("shot_person", who=who_is(persona, environment.at)),
        f"Сцена: {ask.scene}",
        instructions.fill("shot_light", light=environment.light),
        instructions.text("shot_rules"),
    ])


def pick_references(rows: Sequence[PersonaMedia], scene: str, limit: int = MAX_REFERENCES) -> list[PersonaMedia]:
    """The gallery photos to show the model: those whose tags the scene mentions first, then
    the oldest (a stable choice keeps the cache key stable). A tag matches by its stem, so the
    tag «кухня» is found in «на кухне»."""
    words = scene.lower()

    def mentioned(row: PersonaMedia) -> int:
        stems = (tag.strip().lower()[:max(TAG_STEM_MIN, len(tag.strip()) - 2)] for tag in row.tags or [])
        return sum(1 for stem in stems if len(stem) >= TAG_STEM_MIN and stem in words)

    return sorted(rows, key=lambda row: (-mentioned(row), row.id))[:limit]


REFERENCE_DEMUXERS = {"image/jpeg": "jpeg_pipe", "image/png": "png_pipe", "image/webp": "webp_pipe"}


def planned_reference_ids(value: object, kind: MediaKind) -> list[int] | None:
    limit = 1 if kind is MediaKind.VIDEO_NOTE else MAX_REFERENCES
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        return None
    if any(type(item) is not int or not 0 < item <= 2**31 - 1 for item in value):
        return None
    return value if len(set(value)) == len(value) else None


async def valid_reference_image(data: bytes, mime: str) -> bool:
    """Check the declared MIME and decode one frame using the existing bounded subprocess."""
    try:
        checked_suffix(data, mime)
        with tempfile.TemporaryDirectory(prefix="sim-reference-") as folder:
            source = Path(folder) / "reference"
            source.write_bytes(data)
            code, frame, _ = await run_video_process((
                "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-max_alloc", "16777216",
                "-protocol_whitelist", "file,pipe", "-threads", "1", "-err_detect", "explode",
                "-f", REFERENCE_DEMUXERS[mime], "-i", str(source), "-frames:v", "1",
                "-filter_threads", "1", "-vf", "scale=8:8", "-pix_fmt", "gray",
                "-threads", "1", "-f", "rawvideo", "pipe:1",
            ), b"", 8)
    except (AudioError, MediaRejected, OSError):
        return False
    return code == 0 and len(frame) == 64


def photo_key(persona_id: int, prompt: str, model: str, references: Sequence[ImageReference]) -> str:
    """The hash of everything that changes the picture."""
    return hashlib.sha256(json.dumps({
        "persona_id": persona_id, "prompt": prompt, "model": model,
        "references": [hashlib.sha256(ref.data).hexdigest() for ref in references],
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class PersonaMediaTool:
    def __init__(
        self,
        session: AsyncSession,
        images: ImagePort | None,
        notes: OwnerVideoNotes,
        media: MediaStore,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self.session = session
        self.images = images
        self.notes = notes
        self.media = media
        self.clock = clock
        self.jobs = JobRepo(session)
        self.gallery = PersonaMediaRepo(session)
        self.settings = AppSettingsService(session)
        self.decisions = MediaDecisionLog(session, clock=clock)

    # ---- what she may attach -------------------------------------------------------------
    async def offer(self, dialog: Dialog, persona: Persona, port: object | None) -> Offer:
        """`port`: the channel port of the dialog (what the channel can send), None when it
        cannot be built."""
        cfg = await self.settings.load()
        gap = 0 if is_owner_account(dialog, cfg.instant_reply_telegram_ids) else cfg.persona_media_gap_min
        if await self.busy(dialog, gap):
            return Offer()
        can = getattr(port, "capabilities", frozenset())
        photo = cfg.persona_photo_model and self.images is not None and Capability.SEND_PHOTO in can
        video = (self.notes.video is not None and Capability.SEND_VIDEO_NOTE in can
                 and owner_allowed(dialog, cfg.video_note_owner_telegram_id))
        if not (photo or video) or not await self.references(persona.id):
            return Offer()
        kinds = []
        if photo:
            kinds.append(MediaKind.PHOTO)
        if video:
            kinds.append(MediaKind.VIDEO_NOTE)
        return Offer(tuple(kinds))

    async def busy(self, dialog: Dialog, gap_min: int) -> bool:
        """A generation is pending or running, or one was sent or failed within the gap."""
        if await self.jobs.open_by_key(jobkinds.persona_media_key(dialog.id)) is not None:
            return True
        if gap_min <= 0:
            return False
        generated = or_(Message.media_url.like(f"{MEDIA_URL}{AI_PHOTO_PREFIX}%"),
                        Message.media_url.like(f"{MEDIA_URL}{AI_VIDEO_PREFIX}%"))
        failed = and_(Message.delivery == Delivery.FAILED,
                      Message.text.in_((PHOTO_FAILED, NOTE_FAILED)))
        return await self.session.scalar(
            select(Message.id).where(
                Message.dialog_id == dialog.id, Message.direction == Direction.OUT,
                Message.created_at >= self.clock() - timedelta(minutes=gap_min),
                or_(generated, failed),
            ).limit(1)
        ) is not None

    async def references(self, persona_id: int) -> list[PersonaMedia]:
        """Her gallery photos that can be shown to a model: the file is there, it is a JPEG, PNG
        or WebP and it is not too large."""
        rows = await self.gallery.for_persona(persona_id, kind=MediaKind.PHOTO)
        return [row for row in rows if self.reference_source(row) is not None]

    def reference_source(self, row: PersonaMedia) -> tuple[Path, str] | None:
        """(file, mime) of a candidate reference; unrelated bad files are skipped."""
        mime = REFERENCE_MIMES.get(row.storage_name.rsplit(".", 1)[-1].lower())
        try:
            path = self.media.path(row.storage_name)
            if mime is not None and path is not None and 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
                return path, mime
        except OSError:
            pass
        return None

    async def planned_references(self, persona_id: int, ids: list[int]) -> list[Reference] | None:
        rows = await self.session.scalars(select(PersonaMedia).where(
            PersonaMedia.id.in_(ids), PersonaMedia.persona_id == persona_id,
            PersonaMedia.kind == MediaKind.PHOTO,
        ))
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(ids):
            return None
        references = []
        for reference_id in ids:
            source = self.reference_source(by_id[reference_id])
            if source is None:
                return None
            path, mime = source
            try:
                with path.open("rb") as handle:
                    data = handle.read(MAX_REFERENCE_BYTES + 1)
            except OSError:
                return None
            if not 0 < len(data) <= MAX_REFERENCE_BYTES or not await valid_reference_image(data, mime):
                return None
            references.append(Reference(data, mime))
        return references

    async def context(self, dialog: Dialog, persona: Persona, offer: Offer) -> str:
        """The media part of the reply's dynamic context: what she may attach, how she reports a
        decision not to send, and her latest decisions in this dialog."""
        earlier = await self.decisions.brief(dialog.id, persona.timezone)
        cfg = await self.settings.load()
        preferred = (instructions.text("media_priority")
                     if is_owner_account(dialog, cfg.media_priority_telegram_ids) else "")
        return "\n\n".join(filter(None, [offer.brief(), instructions.text("media_report"),
                                       preferred, earlier]))

    # ---- her decision (the reply) --------------------------------------------------------
    async def decide(  # noqa: PLR0913
        self, dialog: Dialog, persona: Persona, decision: Decision, port: object | None,
        *, delay_s: float, final: bool,
    ) -> MediaDecision | None:
        """Journal what she decided about media in this approved reply and queue what she asked
        to send, if it is on offer right now. None: no media was asked for by anyone. `final`:
        the reply ends the conversation, nothing is generated after it."""
        ask = decision.media
        asked = await self.decisions.asked(dialog.id)
        if ask is None and asked is None:
            return None
        offer = await self.offer(dialog, persona, port)
        cfg = await self.settings.load()
        kind = ask.kind if ask is not None else asked
        sends = ask is not None and ask.send
        if ask is None:
            outcome = UNSENT       # she was asked and her reply says nothing about media
        elif not sends:
            outcome = POSTPONED if ask.decision == MEDIA_LATER else DECLINED
        elif final:
            outcome = ENDED
        else:
            outcome = QUEUED if kind in offer.kinds else UNAVAILABLE
        reference_ids: tuple[int, ...] = ()
        environment = None
        if outcome == QUEUED:
            limit = 1 if kind is MediaKind.VIDEO_NOTE else MAX_REFERENCES
            references = pick_references(await self.references(persona.id), ask.scene, limit)
            reference_ids = tuple(reference.id for reference in references)
            if reference_ids:
                environment = ShotEnvironment.capture(persona.timezone, self.clock())
            else:
                outcome = UNAVAILABLE
        reason = ask.reason if ask is not None else ""
        # A refusal is about something she was asked for, even when the request was not recognised.
        requested = asked or (None if sends else kind)
        record = MediaDecision(
            requested=requested.value if requested is not None else NOTHING,
            kind=kind.value,
            decision=ask.decision if ask is not None else SILENT,
            outcome=outcome, reason=reason,
            # with no reason named, the start of her reply shows what she told the client
            reply="" if reason else one_line(decision.reply, REPLY_MAX),
            scene=ask.scene if ask is not None else "",
            owner=owner_allowed(dialog, cfg.video_note_owner_telegram_id),
            offered=tuple(k.value for k in offer.kinds),
            warning=self.warning(ask, persona),
            planned_at_utc=environment.at.isoformat() if environment is not None else None,
            planned_light=environment.light if environment is not None else None,
            reference_ids=reference_ids,
        )
        row = await self.decisions.add(dialog, record)
        if outcome == QUEUED and environment is not None:
            payload = {"dialog_id": dialog.id, "kind": ask.kind.value, "scene": ask.scene, "decision_id": row.id}
            payload["reference_ids"] = list(reference_ids)
            payload["shot_environment"] = environment.payload()
            if ask.kind is MediaKind.VIDEO_NOTE:
                payload["chat_video_target"] = ChatVideoTarget.from_dialog(dialog).payload()
            await self.jobs.enqueue(
                jobkinds.PERSONA_MEDIA,
                payload,
                self.clock() + timedelta(seconds=delay_s + SEND_AFTER_TEXT_S),
                dedup_key=jobkinds.persona_media_key(dialog.id), max_attempts=1,
            )
        elif sends:
            log.info("dialog %s: %s asked for but not queued (%s)", dialog.id, kind.value, outcome)
        return record

    def warning(self, ask: MediaAsk | None, persona: Persona) -> str:
        """What the owner should look at: no decision was named, or the reason for not sending
        (now) is not confirmed by her routine. A decision to send needs no reason."""
        if ask is None:
            return UNDECIDED
        if ask.send:
            return ""
        return reason_warning(ask.reason, persona.routine, persona.timezone, self.clock())

    # ---- the job (worker) ----------------------------------------------------------------
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate (or take from the cache) and queue the send. One attempt: a generation is
        not repeated on failure, the reason stays in the dialog and in the job's result. What
        came of it is written into the record of her decision."""
        reference_ids = self.payload_reference_ids(payload)
        record_id = payload.get("decision_id")
        dialog_id = payload.get("dialog_id")
        if (reference_ids is None or not isinstance(payload.get("scene"), str)
                or type(record_id) is not int or type(dialog_id) is not int):
            return {"outcome": "refused", "error": "invalid media plan identity"}
        record = await self.session.scalar(select(Message).where(
            Message.id == record_id, Message.dialog_id == dialog_id,
        ))
        decision = load(record) if record is not None else None
        if (decision is None or record is None or decision.outcome != QUEUED
                or decision.kind != payload["kind"] or decision.scene != payload.get("scene")
                or decision.reference_ids != tuple(reference_ids)):
            return {"outcome": "refused", "error": "media plan does not match the queued decision"}
        if "shot_environment" in payload:
            error = "invalid or mismatched shot environment"
            environment = ShotEnvironment.parse(payload["shot_environment"])
            if (environment is not None and (environment.at.isoformat() != decision.planned_at_utc
                    or environment.light != decision.planned_light)):
                environment = None
        else:
            error = "legacy shot environment cannot be verified"
            environment = self.legacy_environment(decision)
            if environment is not None:
                await self.decisions.update(record_id, planned_light=environment.light)
        if environment is None:
            result = {"outcome": "refused", "error": error}
        else:
            result = await self.generate(payload, environment, reference_ids)
        await self.decisions.generated(record_id, result)
        return result

    @staticmethod
    def payload_reference_ids(payload: dict[str, Any]) -> list[int] | None:
        if payload.get("kind") not in (MediaKind.PHOTO.value, MediaKind.VIDEO_NOTE.value):
            return None
        return planned_reference_ids(payload.get("reference_ids"), MediaKind(payload["kind"]))

    @staticmethod
    def legacy_environment(decision: MediaDecision) -> ShotEnvironment | None:
        # UTC time is scene metadata. The scoped queued record, not its age or
        # a comparison with another wall clock, authorizes recovery.
        at = ShotEnvironment.utc_stamp(decision.planned_at_utc)
        if at is None or decision.planned_light is not None:
            return None
        return ShotEnvironment(at, LEGACY_SCENE_LIGHT)

    async def generate(
        self, payload: dict[str, Any], environment: ShotEnvironment, reference_ids: list[int],
    ) -> dict[str, Any]:
        dialog = await DialogRepo(self.session).get(payload["dialog_id"])
        persona = await self.session.get(Persona, dialog.client.persona_id) if dialog else None
        if dialog is None or persona is None:
            return {"outcome": "skipped", "error": "dialog or persona is gone"}
        if dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED:
            return {"outcome": "skipped", "error": "the AI is paused or the client refused"}
        ask = MediaAsk(MediaKind(payload["kind"]), payload["scene"])
        prompt = shot_prompt(persona, ask, environment)
        references = await self.planned_references(persona.id, reference_ids)
        if references is None:
            return {"outcome": "refused", "error": "a planned persona reference is invalid or unavailable"}
        if ask.kind is MediaKind.VIDEO_NOTE:
            binding = payload.get("chat_video_target")
            if binding is None:
                return {"outcome": "refused", "error": "chat video request lacks the original recipient binding"}
            target = ChatVideoTarget.from_payload(binding)
            if target != ChatVideoTarget.from_dialog(dialog):
                return {"outcome": "refused", "error": "the original chat video recipient changed"}
            return await self.video_note(
                persona, ask, prompt, references[0], target, payload.get("decision_id"),
            )
        return await self.photo(dialog, persona, ask, prompt, [
            ImageReference(reference.data, reference.mime) for reference in references
        ])

    async def video_note(
        self, persona: Persona, ask: MediaAsk, prompt: str, reference: Reference,
        target: ChatVideoTarget, decision_id: int | None,
    ) -> dict[str, Any]:
        """Prepare a note on the exact allowlisted route that requested it."""
        result = await self.notes.run(VideoNoteRequest(
            persona.id, prompt, NOTE_DURATION_S, NOTE_RESOLUTION,
        ).payload(), target=target, decision_id=decision_id, chat_reference=reference)
        note = await self.session.get(Message, result["message_id"]) if "message_id" in result else None
        if note is not None:
            note.original_text = ask.scene
        return result

    async def photo(
        self, dialog: Dialog, persona: Persona, ask: MediaAsk, prompt: str,
        references: list[ImageReference],
    ) -> dict[str, Any]:
        cfg = await self.settings.load()
        try:
            name, cached, model = await self.picture(persona, prompt, cfg, references)
        except ImageError as exc:
            self.session.add(failed_message(dialog, PHOTO_FAILED, str(exc), self.clock()))
            log.warning("dialog %s: persona photo not made: %s", dialog.id, exc)
            return {"outcome": "failed", "error": str(exc)[:300]}
        message = ai_message(
            dialog, "", self.clock(), media_kind=MediaKind.PHOTO.value,
            media_url=MEDIA_URL + name, model=model[:120], original_text=ask.scene,
        )
        self.session.add(message)
        await self.session.flush()
        apply_message(dialog, message)
        await self.jobs.enqueue(jobkinds.SEND, {"message_id": message.id}, self.clock(),
                                dedup_key=jobkinds.send_key(message.id))
        return {"outcome": "queued", "cached": cached, "message_id": message.id}

    async def picture(
        self, persona: Persona, prompt: str, cfg: AppSettings, references: list[ImageReference],
    ) -> tuple[str, bool, str]:
        """(stored file name, was it cached, model). Raises ImageError with the reason."""
        model = cfg.persona_photo_model
        if not model or self.images is None:
            raise ImageError("photo generation is off: the setting «Модель для фото» is empty")
        if not references:
            raise ImageError("the persona gallery has no usable reference photo")
        name = stored_name(AI_PHOTO_PREFIX, photo_key(persona.id, prompt, model, references), "png")
        if self.media.path(name) is not None:
            return name, True, model
        result = await self.images.generate_image(
            prompt, model=model, references=references, workflow=PHOTO_WORKFLOW)
        await self.media.save_as(name, result.image)
        return name, False, result.model or model
