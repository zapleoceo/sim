"""The persona's own tool for photos and video notes: during a chat she decides that a photo or
a video note fits, and the system generates it from her gallery (reference photos) and her life
at this moment.

How it works:
- `offer()` says what she may attach to this reply. The answer goes into the reply's dynamic
  context as one block (`Offer.brief()`): either how to ask (a `media` field in her JSON answer),
  or that she cannot send media now and must not promise it;
- she asks with `{"kind": "photo" | "video_note", "scene": "..."}` (`prompt.MediaAsk`);
- `request()` re-checks the offer and queues the `persona_media` job after her text;
- `run()` (worker) builds the prompt: her scene plus her life right now, computed by code from
  her time zone and weekly routine (`shot_prompt`), takes reference photos from the gallery and
  has the Broker generate the photo (`ImagePort`) or the video note (`OwnerVideoNotes`: the same
  cache, encoder and allowlist as the owner's own notes).

Rules that do not change:
- a generated photo is sent with the caption "Сгенерировано ИИ" (`OutboundService` forces it);
- a video note goes only to the account from the setting «Кружочки ИИ», after that label;
- her text never promises the media: it is a separate message that may not come. When the
  generation fails, the reason is a failed, never-sent message in the dialog and the result of
  the job in the system log.

What the offer needs. A photo: the setting «Модель для фото» (`persona_photo_model`), at least
one usable photo in her gallery, a channel that can send photos. A video note: the dialog with
the allowlisted account and a channel that can send notes. Both: no generated media in this
dialog within «Пауза между медиа» (`persona_media_gap_min`)."""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Dialog, Message, Persona, PersonaMedia
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.repositories.persona_media import PersonaMediaRepo
from sim.adapters.media import MediaStore
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus, Delivery, Direction, MediaKind
from sim.ports.image import ImageError, ImagePort, ImageReference
from sim.services import jobkinds
from sim.services.ai_media import (
    AI_LABEL,
    AI_PHOTO_PREFIX,
    AI_VIDEO_PREFIX,
    MEDIA_URL,
    ai_message,
    failed_message,
    stored_name,
)
from sim.services.app_settings import AppSettings, AppSettingsService
from sim.services.dialog_state import apply_message
from sim.services.persona_clock import WEEKDAYS, age_on, day_part, local_time
from sim.services.persona_routine import doing_now
from sim.services.prompt import MediaAsk
from sim.services.video_note import (
    MAX_REFERENCE_BYTES,
    NOTE_FAILED,
    REFERENCE_MIMES,
    OwnerVideoNotes,
    VideoNoteRequest,
    owner_allowed,
)

log = logging.getLogger(__name__)

PHOTO_WORKFLOW = "persona-photo"
PHOTO_FAILED = "[фото ИИ не создано]"
MAX_REFERENCES = 3               # the Broker takes at most 5 images and 10 MB per request
SEND_AFTER_TEXT_S = 2.0          # the media follows her last text message
NOTE_DURATION_S = 5
NOTE_RESOLUTION = "360p"
TAG_STEM_MIN = 4                 # letters of a gallery tag compared with the scene

KIND_NAMES = {MediaKind.PHOTO: "своё фото", MediaKind.VIDEO_NOTE: "свой кружочек (короткое видео)"}
NO_MEDIA = (
    "МЕДИА: сейчас ты не можешь отправить фото или кружочек. Не обещай их прислать и не пиши, "
    "что отправила."
)
MEDIA_BRIEF = (
    "МЕДИА: к этому ответу ты можешь приложить {kinds}. Решаешь сама: редко и только когда это "
    "уместно в разговоре (попросили показать, или кадр скажет больше слов). Для этого добавь в "
    "JSON поле \"media\": {{\"kind\": {values}, \"scene\": \"что в кадре: где ты, что делаешь, во "
    "что одета, настроение; для кружочка ещё что говоришь\"}}. Кадр должен совпадать с блоком "
    "ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС (время суток, где ты по распорядку) и с твоей жизнью. Только "
    "приличные бытовые кадры, без других людей и документов. Медиа уйдёт отдельным сообщением "
    "после текста и может не прийти: в reply не обещай его и не пиши, что отправила, текст должен "
    "быть уместен сам по себе. Без повода поле media не добавляй."
)
FRAMES = {
    MediaKind.PHOTO: "Живое любительское фото для личной переписки, снято на смартфон. Без "
                     "текста, надписей и водяных знаков.",
    MediaKind.VIDEO_NOTE: "Короткое видео-селфи для личной переписки (кружочек), снято на "
                          "фронтальную камеру смартфона, лицо в центре кадра.",
}
WHO = {"female": "женщина", "male": "мужчина"}


@dataclass(frozen=True)
class Offer:
    """What the persona may attach to this reply."""

    kinds: tuple[MediaKind, ...] = ()

    def brief(self) -> str:
        """The block of the reply's dynamic context."""
        if not self.kinds:
            return NO_MEDIA
        return MEDIA_BRIEF.format(
            kinds=" или ".join(KIND_NAMES[kind] for kind in self.kinds),
            values=" или ".join(f'"{kind.value}"' for kind in self.kinds),
        )


def life_now(persona: Persona, now_utc: datetime) -> str:
    """Her moment for the shot: weekday, part of the day and what the routine has her doing."""
    local = local_time(persona.timezone, now_utc)
    doing = "; ".join(doing_now(persona.routine, local)) or "свободное время"
    return f"{WEEKDAYS[local.weekday()]}, {day_part(local.hour)}; по распорядку сейчас: {doing}"


def who_is(persona: Persona, now_utc: datetime) -> str:
    who = WHO.get(persona.gender, "человек")
    if persona.birth_date is None:
        return who
    return f"{who}, возраст {age_on(persona.birth_date, local_time(persona.timezone, now_utc).date())}"


def shot_prompt(persona: Persona, ask: MediaAsk, now_utc: datetime) -> str:
    """What the image or video model gets: the kind of shot, who is in it (the references show
    how she looks), the scene she asked for and her life at this moment. No minutes or dates in
    it, so the same request within the same part of her day is the same prompt (cache)."""
    return "\n".join([
        FRAMES[ask.kind],
        f"В кадре {who_is(persona, now_utc)}: тот же человек, что на приложенных фото. Лицо, "
        "возраст, причёску и телосложение сохрани точно.",
        f"Сцена: {ask.scene}",
        f"Жизнь этого человека в этот момент: {life_now(persona, now_utc)}. Время суток, место и "
        "занятие в кадре должны этому соответствовать.",
        "Кадр приличный и бытовой: без обнажённости, без других узнаваемых людей, без документов.",
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

    # ---- what she may attach -------------------------------------------------------------
    async def offer(self, dialog: Dialog, persona: Persona, port: object | None) -> Offer:
        """`port`: the channel port of the dialog (what the channel can send), None when it
        cannot be built."""
        cfg = await self.settings.load()
        if await self.busy(dialog, cfg.persona_media_gap_min):
            return Offer()
        can = getattr(port, "capabilities", frozenset())
        kinds = []
        if (cfg.persona_photo_model and self.images is not None and Capability.SEND_PHOTO in can
                and await self.references(persona.id)):
            kinds.append(MediaKind.PHOTO)
        if (self.notes.video is not None and Capability.SEND_VIDEO_NOTE in can
                and owner_allowed(dialog, cfg.video_note_owner_telegram_id)):
            kinds.append(MediaKind.VIDEO_NOTE)
        return Offer(tuple(kinds))

    async def busy(self, dialog: Dialog, gap_min: int) -> bool:
        """A generation is queued for this dialog, or one was sent or failed within the gap."""
        if await self.jobs.pending_by_key(jobkinds.persona_media_key(dialog.id)) is not None:
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
        """(file, mime) of a usable reference photo; the file is not read here."""
        mime = REFERENCE_MIMES.get(row.storage_name.rsplit(".", 1)[-1].lower())
        path = self.media.path(row.storage_name)
        if mime is None or path is None or not 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
            return None
        return path, mime

    def reference_files(self, rows: Sequence[PersonaMedia]) -> list[ImageReference]:
        sources = (self.reference_source(row) for row in rows)
        return [ImageReference(path.read_bytes(), mime) for path, mime in filter(None, sources)]

    # ---- her request (the reply) ---------------------------------------------------------
    async def request(
        self, dialog: Dialog, persona: Persona, ask: MediaAsk, port: object | None, *, delay_s: float,
    ) -> bool:
        """Queue what she asked for, if it is on offer right now. False: nothing is queued."""
        if ask.kind not in (await self.offer(dialog, persona, port)).kinds:
            log.info("dialog %s: %s asked for but not on offer", dialog.id, ask.kind.value)
            return False
        await self.jobs.enqueue(
            jobkinds.PERSONA_MEDIA,
            {"dialog_id": dialog.id, "kind": ask.kind.value, "scene": ask.scene},
            self.clock() + timedelta(seconds=delay_s + SEND_AFTER_TEXT_S),
            dedup_key=jobkinds.persona_media_key(dialog.id), max_attempts=1,
        )
        return True

    # ---- the job (worker) ----------------------------------------------------------------
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate (or take from the cache) and queue the send. One attempt: a generation is
        not repeated on failure, the reason stays in the dialog and in the job's result."""
        dialog = await DialogRepo(self.session).get(int(payload["dialog_id"]))
        persona = await self.session.get(Persona, dialog.client.persona_id) if dialog else None
        if dialog is None or persona is None:
            return {"outcome": "skipped", "error": "dialog or persona is gone"}
        if dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED:
            return {"outcome": "skipped", "error": "the AI is paused or the client refused"}
        ask = MediaAsk(MediaKind(payload["kind"]), str(payload["scene"]))
        prompt = shot_prompt(persona, ask, self.clock())
        rows = pick_references(await self.references(persona.id), ask.scene)
        if ask.kind is MediaKind.VIDEO_NOTE:
            return await self.video_note(dialog, persona, ask, prompt, rows)
        return await self.photo(dialog, persona, ask, prompt, rows)

    async def video_note(
        self, dialog: Dialog, persona: Persona, ask: MediaAsk, prompt: str, rows: list[PersonaMedia],
    ) -> dict[str, Any]:
        """The owner's video-note pipeline with her prompt: allowlist, cache, label, note."""
        cfg = await self.settings.load()
        if not owner_allowed(dialog, cfg.video_note_owner_telegram_id):
            return {"outcome": "refused", "error": "the dialog is not with the allowlisted account"}
        result = await self.notes.run(VideoNoteRequest(
            persona.id, prompt, NOTE_DURATION_S, NOTE_RESOLUTION, rows[0].id if rows else None,
        ).payload())
        note = await self.session.get(Message, result["message_id"]) if "message_id" in result else None
        if note is not None:
            note.original_text = ask.scene
        return result

    async def photo(
        self, dialog: Dialog, persona: Persona, ask: MediaAsk, prompt: str, rows: list[PersonaMedia],
    ) -> dict[str, Any]:
        cfg = await self.settings.load()
        references = self.reference_files(rows)
        try:
            name, cached, model = await self.picture(persona, prompt, cfg, references)
        except ImageError as exc:
            self.session.add(failed_message(dialog, PHOTO_FAILED, str(exc), self.clock()))
            log.warning("dialog %s: persona photo not made: %s", dialog.id, exc)
            return {"outcome": "failed", "error": str(exc)[:300]}
        message = ai_message(
            dialog, AI_LABEL, self.clock(), media_kind=MediaKind.PHOTO.value,
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
