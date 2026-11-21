"""`{"type": "photo"}` and `{"type": "video"}`: a ready file from the catalog goes out as is; a
`generate` request becomes a generation job (sim/services/generation.py) whose result is sent
when ready. The text under a generated file goes out right away as a text message, so the
client is not left waiting in silence."""
from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from sim.domain.enums import Author, GenerationKind, MessageKind
from sim.domain.plan import PhotoAction, VideoAction
from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.actions.text import enqueue_text
from sim.services.generation import start_generation
from sim.services.outbox import enqueue_message


class MediaHandler(ActionHandler):
    message_kind: ClassVar[MessageKind]
    generation_kind: ClassVar[GenerationKind]

    async def apply(self, session: AsyncSession, action: Any, prepared: Any,
                    ctx: ActionContext, result: ApplyResult) -> None:
        if action.media_id is not None:
            message = await enqueue_message(
                session, chat_id=ctx.chat_id, author=Author.PERSONA, kind=self.message_kind,
                text=action.caption, media_id=action.media_id, reply_run_id=ctx.reply_run_id,
            )
            result.message_ids.append(message.id)
            return
        if action.caption:
            await enqueue_text(session, ctx, result, action.caption)
        generation = await start_generation(
            session, persona=ctx.persona, chat_id=ctx.chat_id, reply_run_id=ctx.reply_run_id,
            kind=self.generation_kind, spec=action.generate,
        )
        result.generation_ids.append(generation.id)


class PhotoHandler(MediaHandler):
    action_type = PhotoAction
    message_kind = MessageKind.PHOTO
    generation_kind = GenerationKind.PHOTO


class VideoHandler(MediaHandler):
    action_type = VideoAction
    message_kind = MessageKind.VIDEO
    generation_kind = GenerationKind.VIDEO
