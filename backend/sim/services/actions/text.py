"""`{"type": "text"}`: one pending text message, typed by her «phone» (docs/writing.md)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.domain.enums import Author, MessageKind
from sim.domain.plan import TextAction
from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.outbox import enqueue_message
from sim.services.writing import humanize, message_rng


async def enqueue_text(session: AsyncSession, ctx: ActionContext, result: ApplyResult,
                       text: str) -> None:
    message = await enqueue_message(session, chat_id=ctx.chat_id, author=Author.PERSONA,
                                    kind=MessageKind.TEXT, text=text,
                                    reply_run_id=ctx.reply_run_id)
    result.message_ids.append(message.id)


class TextHandler(ActionHandler):
    action_type = TextAction

    async def apply(self, session: AsyncSession, action: Any, prepared: Any,
                    ctx: ActionContext, result: ApplyResult) -> None:
        rng = message_rng(ctx.reply_run_id, len(result.message_ids))
        await enqueue_text(session, ctx, result, humanize(action.text, ctx.writing, rng))
