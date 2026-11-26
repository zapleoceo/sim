"""`/api/personas/{id}/stickers`: her stickers (docs/stickers.md) — what her Telegram accounts
have and she may send, and «Обновить» (re-read the accounts now; it also happens on every
reconnect of an account)."""
from __future__ import annotations

from typing import Any

from litestar import get, post
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.context import ApiContext
from sim.api.queries import persona_or_404
from sim.db.models import Channel, PersonaSticker
from sim.domain.enums import ChannelKind

Json = dict[str, Any]


def sticker_view(row: PersonaSticker) -> Json:
    return {"id": row.id, "channel_id": row.channel_id, "emoji": row.emoji,
            "set_name": row.set_name, "set_title": row.set_title, "faved": row.faved}


@get("/personas/{persona_id:int}/stickers")
async def list_stickers(persona_id: int, db: AsyncSession) -> Json:
    await persona_or_404(db, persona_id)
    rows = (await db.scalars(
        select(PersonaSticker).where(PersonaSticker.persona_id == persona_id)
        .order_by(PersonaSticker.faved.desc(), PersonaSticker.position,
                  PersonaSticker.id))).all()
    return {"items": [sticker_view(row) for row in rows]}


@post("/personas/{persona_id:int}/stickers/sync", status_code=202)
async def sync_stickers(persona_id: int, db: AsyncSession, ctx: ApiContext) -> Json:
    """Queues a re-read of every Telegram account of hers (the telegram process does it)."""
    await persona_or_404(db, persona_id)
    channels = list(await db.scalars(
        select(Channel.id).where(Channel.persona_id == persona_id,
                                 Channel.kind == ChannelKind.TELEGRAM_USER)
        .order_by(Channel.id)))
    for channel_id in channels:
        await ctx.deps.tasks.sync_stickers(channel_id)
    return {"channels": channels}


ROUTES = [list_stickers, sync_stickers]
