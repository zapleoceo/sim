"""Her stickers (docs/stickers.md): the ones her Telegram account has — installed sets and
favourites — are kept in `persona_sticker` (written by the telegram process after it reads the
account) and offered to the reply model as a short catalog «id эмодзи (набор)». No stickers on
her account: no catalog, no sticker action — she simply writes."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, PersonaSticker
from sim.ports import StickerRef

MAX_CATALOG = 60                    # stickers offered to one turn: favourites first
STICKERS_HEADING = "# Стикеры (можешь отправить)"


@dataclass(frozen=True)
class AccountSticker:
    """One sticker as read from her account."""

    doc_id: int
    set_id: int
    set_access_hash: int
    emoji: str = ""
    set_name: str = ""
    set_title: str = ""
    faved: bool = False
    position: int = 0


def ref_of(row: PersonaSticker) -> StickerRef:
    return StickerRef(doc_id=row.doc_id, set_id=row.set_id, set_access_hash=row.set_access_hash)


async def save_account_stickers(session: AsyncSession, *, persona_id: int, channel_id: int,
                                stickers: Sequence[AccountSticker]) -> int:
    """The account's stickers replace what was stored for this channel (same doc -> same row,
    so a sent message keeps its link). Returns how many there are now."""
    rows = {row.doc_id: row for row in (await session.scalars(
        select(PersonaSticker).where(PersonaSticker.channel_id == channel_id))).all()}
    seen: set[int] = set()
    for sticker in stickers:
        if sticker.doc_id in seen:
            continue
        seen.add(sticker.doc_id)
        row = rows.get(sticker.doc_id)
        if row is None:
            row = PersonaSticker(persona_id=persona_id, channel_id=channel_id,
                                 doc_id=sticker.doc_id)
            session.add(row)
        row.persona_id = persona_id
        row.set_id, row.set_access_hash = sticker.set_id, sticker.set_access_hash
        row.set_name, row.set_title = sticker.set_name[:100], sticker.set_title[:200]
        row.emoji, row.faved, row.position = sticker.emoji[:32], sticker.faved, sticker.position
    for doc_id, row in rows.items():
        if doc_id not in seen:
            await session.delete(row)
    await session.flush()
    return len(seen)


async def chat_stickers(session: AsyncSession, chat: Chat) -> list[PersonaSticker]:
    """The stickers she can send in this chat: her account on the chat's channel, favourites
    first, then by set order; at most `MAX_CATALOG`."""
    return list((await session.scalars(
        select(PersonaSticker).where(PersonaSticker.channel_id == chat.channel_id,
                                     PersonaSticker.persona_id == chat.persona_id)
        .order_by(PersonaSticker.faved.desc(), PersonaSticker.position, PersonaSticker.id)
        .limit(MAX_CATALOG))).all())


def stickers_block(rows: Sequence[PersonaSticker]) -> str:
    """«- 17 😂 (Утята)» per sticker; "" when she has none."""
    if not rows:
        return ""
    lines = [f"- {row.id} {row.emoji or '·'} ({row.set_title or row.set_name or 'избранное'})"
             for row in rows]
    return f"{STICKERS_HEADING}\n" + "\n".join(lines)
