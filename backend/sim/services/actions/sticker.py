"""`{"type": "sticker"}`: one of her account's stickers (docs/stickers.md) — a pending
outgoing message of kind sticker that points at the sticker; the emoji is its text, so the
timeline and the dashboard can say which one it was."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, PersonaSticker
from sim.domain.enums import Author, MessageKind
from sim.domain.plan import StickerAction
from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.outbox import enqueue_message


class StickerHandler(ActionHandler):
    action_type = StickerAction

    async def apply(self, session: AsyncSession, action: Any, prepared: Any,
                    ctx: ActionContext, result: ApplyResult) -> None:
        sticker = await session.get(PersonaSticker, action.sticker_id)
        chat = await session.get(Chat, ctx.chat_id)
        if (sticker is None or chat is None or sticker.persona_id != ctx.persona.id
                or sticker.channel_id != chat.channel_id):
            result.notes.append(f"стикер {action.sticker_id} пропал, не отправлен")
            return
        message = await enqueue_message(session, chat_id=ctx.chat_id, author=Author.PERSONA,
                                        kind=MessageKind.STICKER, text=sticker.emoji,
                                        reply_run_id=ctx.reply_run_id)
        message.sticker_id = sticker.id
        result.message_ids.append(message.id)
