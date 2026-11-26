"""Her stickers on Telegram (docs/stickers.md): what her account has, and sending one.

`account_stickers(client)` reads the account's favourite stickers and installed sets (at most
`MAX_SETS`, `MAX_PER_SET` each) with their emoji. `StickerSync.attach` is a runtime hook: on
every fresh connection of an account it re-reads them in the background and stores them for
the persona (`services/stickers.save_account_stickers`). `sticker_media(client, ref)` gives
what `SendMediaRequest` needs: the document with a fresh file reference, read from its set
(Telegram expires the references it hands out)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import Channel
from sim.db.session import transaction
from sim.ports import PortError, StickerRef
from sim.services.stickers import AccountSticker, save_account_stickers

log = logging.getLogger(__name__)

MAX_SETS = 12
MAX_PER_SET = 40


def emoji_of(document: Any) -> str:
    """The emoji a sticker document stands for (its sticker attribute)."""
    for attr in getattr(document, "attributes", None) or []:
        alt = getattr(attr, "alt", None)
        if isinstance(alt, str) and alt and type(attr).__name__ == "DocumentAttributeSticker":
            return alt
    return ""


def set_of(document: Any) -> tuple[int, int] | None:
    """(set id, access hash) of a sticker document, when it says."""
    for attr in getattr(document, "attributes", None) or []:
        stickerset = getattr(attr, "stickerset", None)
        set_id = getattr(stickerset, "id", None)
        access_hash = getattr(stickerset, "access_hash", None)
        if isinstance(set_id, int) and isinstance(access_hash, int):
            return set_id, access_hash
    return None


async def account_stickers(client: Any) -> list[AccountSticker]:
    """Favourites first, then the installed sets in the account's order."""
    from telethon import functions, types

    found: list[AccountSticker] = []
    faved = await client(functions.messages.GetFavedStickersRequest(hash=0))
    for position, document in enumerate(getattr(faved, "stickers", None) or []):
        owner = set_of(document)
        if owner is not None:
            found.append(AccountSticker(doc_id=document.id, set_id=owner[0],
                                        set_access_hash=owner[1], emoji=emoji_of(document),
                                        faved=True, position=position))
    installed = await client(functions.messages.GetAllStickersRequest(hash=0))
    for set_index, sticker_set in enumerate((getattr(installed, "sets", None) or [])[:MAX_SETS]):
        full = await client(functions.messages.GetStickerSetRequest(
            stickerset=types.InputStickerSetID(id=sticker_set.id,
                                               access_hash=sticker_set.access_hash),
            hash=0))
        emoji = {doc_id: pack.emoticon for pack in getattr(full, "packs", None) or []
                 for doc_id in pack.documents}
        for index, document in enumerate((getattr(full, "documents", None) or [])[:MAX_PER_SET]):
            found.append(AccountSticker(
                doc_id=document.id, set_id=sticker_set.id,
                set_access_hash=sticker_set.access_hash,
                emoji=emoji.get(document.id) or emoji_of(document),
                set_name=str(getattr(sticker_set, "short_name", "") or ""),
                set_title=str(getattr(sticker_set, "title", "") or ""),
                position=(set_index + 1) * 1000 + index))
    return found


async def sticker_media(client: Any, ref: StickerRef) -> Any:
    """`InputMediaDocument` of the sticker with a fresh file reference."""
    from telethon import functions, types

    full = await client(functions.messages.GetStickerSetRequest(
        stickerset=types.InputStickerSetID(id=ref.set_id, access_hash=ref.set_access_hash),
        hash=0))
    for document in getattr(full, "documents", None) or []:
        if document.id == ref.doc_id:
            return types.InputMediaDocument(id=types.InputDocument(
                id=document.id, access_hash=document.access_hash,
                file_reference=document.file_reference))
    raise PortError("the sticker is no longer in its set")


class StickerSync:
    """Runtime hook: her stickers are re-read on every fresh connection of her account."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self.tasks: set[asyncio.Task[None]] = set()

    async def attach(self, channel_id: int, client: Any) -> None:
        task = asyncio.ensure_future(self.sync(channel_id, client))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def sync(self, channel_id: int, client: Any) -> int | None:
        """Read and store; returns how many she has, None when it did not work (logged)."""
        try:
            stickers = await account_stickers(client)
            async with transaction(self.sessions) as session:
                channel = await session.get(Channel, channel_id)
                if channel is None:
                    return None
                count = await save_account_stickers(session, persona_id=channel.persona_id,
                                                    channel_id=channel_id, stickers=stickers)
        except Exception as exc:
            log.warning("telegram channel %s: stickers not read: %s", channel_id,
                        type(exc).__name__)
            return None
        log.info("telegram channel %s: %s stickers", channel_id, count)
        return count
