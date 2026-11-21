"""`{"type": "photo"}`, `{"type": "video"}` and `{"type": "video_note"}`: a ready file from the
catalog goes out as is (a video note is first cut to a circle); a `generate` request becomes a
generation job (sim/services/generation.py) whose result is sent when ready, while the client
sees «отправляет фото / видео» or «записывает кружок». A text under a generated photo / video,
if the model gave one, goes out right away as a text message."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import MediaAsset
from sim.db.session import transaction
from sim.domain.enums import Author, GenerationKind, MediaKind, MediaSource, MessageKind
from sim.domain.plan import PhotoAction, VideoAction, VideoNoteAction
from sim.domain.presence import Busy, busy_with
from sim.ports import PortError
from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.actions.text import enqueue_text
from sim.services.generation import VIDEO_NOTE_MIME, start_generation
from sim.services.media_library import add_media
from sim.services.outbox import enqueue_message

VIDEO_NOTE_TAG = "кружок"


class MediaHandler(ActionHandler):
    message_kind: ClassVar[MessageKind]
    generation_kind: ClassVar[GenerationKind]

    def busy(self, action: Any) -> Busy | None:
        return busy_with(self.message_kind) if action.generate is not None else None

    async def apply(self, session: AsyncSession, action: Any, prepared: Any,
                    ctx: ActionContext, result: ApplyResult) -> None:
        if action.media_id is not None:
            await self.send_ready(session, action, prepared, ctx, result)
            return
        caption = getattr(action, "caption", "")
        if caption:
            await enqueue_text(session, ctx, result, caption)
        generation = await start_generation(
            session, persona=ctx.persona, chat_id=ctx.chat_id, reply_run_id=ctx.reply_run_id,
            kind=self.generation_kind, spec=action.generate,
        )
        result.generation_ids.append(generation.id)

    async def send_ready(self, session: AsyncSession, action: Any, prepared: Any,
                         ctx: ActionContext, result: ApplyResult) -> None:
        """A file from the catalog, sent as it is."""
        await self.enqueue(session, ctx, result, self.message_kind, action.media_id,
                           getattr(action, "caption", ""))

    @staticmethod
    async def enqueue(session: AsyncSession, ctx: ActionContext, result: ApplyResult,
                      kind: MessageKind, media_id: int, text: str = "") -> None:
        message = await enqueue_message(
            session, chat_id=ctx.chat_id, author=Author.PERSONA, kind=kind, text=text,
            media_id=media_id, reply_run_id=ctx.reply_run_id,
        )
        result.message_ids.append(message.id)


class PhotoHandler(MediaHandler):
    action_type = PhotoAction
    message_kind = MessageKind.PHOTO
    generation_kind = GenerationKind.PHOTO


class VideoHandler(MediaHandler):
    action_type = VideoAction
    message_kind = MessageKind.VIDEO
    generation_kind = GenerationKind.VIDEO


@dataclass(frozen=True)
class Rounded:
    """A catalog video cut to a circle, ready to be stored and sent."""

    data: bytes
    duration_s: float
    caption: str
    tags: list[str]


@dataclass(frozen=True)
class RoundFailed:
    error: str


class VideoNoteHandler(MediaHandler):
    """A ready video becomes a round copy (`tools.to_video_note`) before the turn commits; the
    copy is stored as a generated video and sent as `video_note`. If cutting fails the original
    goes out as an ordinary video (noted in the run)."""

    action_type = VideoNoteAction
    message_kind = MessageKind.VIDEO_NOTE
    generation_kind = GenerationKind.VIDEO_NOTE

    async def prepare(self, action: Any, ctx: ActionContext) -> Rounded | RoundFailed | None:
        if action.media_id is None:
            return None
        deps = ctx.deps
        try:
            async with transaction(deps.sessions) as session:
                asset = await session.get(MediaAsset, action.media_id)
                if asset is None:
                    return RoundFailed(f"media {action.media_id} not found")
                key, caption, tags = asset.storage_key, asset.caption, list(asset.tags or [])
            note, duration = await deps.tools.to_video_note(await deps.storage.get(key))
        except PortError as exc:
            return RoundFailed(str(exc) or type(exc).__name__)
        return Rounded(data=note, duration_s=duration, caption=caption,
                       tags=[*tags, VIDEO_NOTE_TAG])

    async def send_ready(self, session: AsyncSession, action: Any, prepared: Any,
                         ctx: ActionContext, result: ApplyResult) -> None:
        if not isinstance(prepared, Rounded):
            error = prepared.error if isinstance(prepared, RoundFailed) else "not prepared"
            result.notes.append(f"кружок не получился ({error}), отправлено обычным видео")
            await self.enqueue(session, ctx, result, MessageKind.VIDEO, action.media_id)
            return
        deps = ctx.deps
        media = await add_media(
            session, deps.storage, deps.tools, persona_id=ctx.persona.id, data=prepared.data,
            mime=VIDEO_NOTE_MIME, file_name=f"video_note-from-{action.media_id}.mp4",
            source=MediaSource.GENERATED, kind=MediaKind.VIDEO, caption=prepared.caption,
            tags=prepared.tags,
        )
        if media.duration_s is None:
            media.duration_s = prepared.duration_s
        await self.enqueue(session, ctx, result, MessageKind.VIDEO_NOTE, media.id)
