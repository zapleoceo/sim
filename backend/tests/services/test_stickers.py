"""Her stickers (docs/stickers.md): the account's stickers stored per channel, the «Стикеры»
catalog of a chat and the sticker action of a plan."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Message, Persona, PersonaSticker
from sim.domain.enums import Author, Direction, MessageKind, MessageStatus
from sim.domain.plan import StickerAction
from sim.ports import StickerRef
from sim.services import stickers as sticker_service
from sim.services.actions import ApplyResult
from sim.services.actions.sticker import StickerHandler
from sim.services.deps import Deps
from sim.services.stickers import (
    STICKERS_HEADING,
    AccountSticker,
    chat_stickers,
    ref_of,
    save_account_stickers,
    stickers_block,
)
from tests.services.factories import all_rows, make_channel, make_chat, make_sticker, olya_chat
from tests.services.test_actions import context, messages_of


def account(doc_id: int, *, emoji: str = "😂", faved: bool = False, position: int = 0,
            set_title: str = "Утята") -> AccountSticker:
    return AccountSticker(doc_id=doc_id, set_id=10, set_access_hash=20, emoji=emoji,
                          set_name="ducks", set_title=set_title, faved=faved, position=position)


async def persona_of(db: AsyncSession, chat: Chat) -> Persona:
    persona = await db.get(Persona, chat.persona_id)
    assert persona is not None
    return persona


async def stored(db: AsyncSession, channel_id: int) -> list[PersonaSticker]:
    return await all_rows(db, select(PersonaSticker)
                          .where(PersonaSticker.channel_id == channel_id)
                          .order_by(PersonaSticker.doc_id))


# --- the account's stickers ------------------------------------------------------------------


async def test_saving_replaces_the_channels_stickers_and_keeps_the_rows_it_still_has(
        db: AsyncSession) -> None:
    chat = await make_chat(db)
    persona_id, channel_id = chat.persona_id, chat.channel_id
    count = await save_account_stickers(
        db, persona_id=persona_id, channel_id=channel_id,
        stickers=[account(1), account(2, faved=True), account(1, emoji="🙂")])
    assert count == 2                                   # the same document once: the first
    first = await stored(db, channel_id)
    assert [(r.doc_id, r.emoji, r.faved, r.persona_id) for r in first] == [
        (1, "😂", False, persona_id), (2, "😂", True, persona_id)]
    kept_id = first[1].id

    count = await save_account_stickers(
        db, persona_id=persona_id, channel_id=channel_id,
        stickers=[account(2, emoji="🥰", position=5, set_title="Котики"), account(3)])
    assert count == 2
    second = await stored(db, channel_id)
    assert [r.doc_id for r in second] == [2, 3]         # doc 1 left the account: deleted
    kept = second[0]
    assert kept.id == kept_id                           # same doc -> same row
    assert (kept.emoji, kept.position, kept.set_title, kept.faved) == ("🥰", 5, "Котики", False)
    assert (kept.set_id, kept.set_access_hash, kept.set_name) == (10, 20, "ducks")


async def test_saving_touches_only_its_own_channel(db: AsyncSession) -> None:
    chat = await make_chat(db)
    other = await make_channel(db, await persona_of(db, chat))      # her second account
    await save_account_stickers(db, persona_id=chat.persona_id, channel_id=other.id,
                                stickers=[account(1)])
    await save_account_stickers(db, persona_id=chat.persona_id, channel_id=chat.channel_id,
                                stickers=[account(1), account(2)])
    assert await save_account_stickers(db, persona_id=chat.persona_id,
                                       channel_id=chat.channel_id, stickers=[]) == 0
    assert await stored(db, chat.channel_id) == []
    assert [r.doc_id for r in await stored(db, other.id)] == [1]


async def test_long_names_are_cut_to_the_columns(db: AsyncSession) -> None:
    chat = await make_chat(db)
    long = AccountSticker(doc_id=1, set_id=1, set_access_hash=1, emoji="😂" * 40,
                          set_name="n" * 150, set_title="т" * 300)
    await save_account_stickers(db, persona_id=chat.persona_id, channel_id=chat.channel_id,
                                stickers=[long])
    [row] = await stored(db, chat.channel_id)
    assert (len(row.emoji), len(row.set_name), len(row.set_title)) == (32, 100, 200)


async def test_a_sent_message_keeps_its_sticker_while_the_account_has_it(
        db: AsyncSession) -> None:
    chat = await make_chat(db)
    await save_account_stickers(db, persona_id=chat.persona_id, channel_id=chat.channel_id,
                                stickers=[account(1), account(2)])
    one, two = await stored(db, chat.channel_id)
    messages = [Message(chat_id=chat.id, direction=Direction.OUT, author=Author.PERSONA,
                        kind=MessageKind.STICKER, text="😂", status=MessageStatus.SENT,
                        sticker_id=row.id) for row in (one, two)]
    db.add_all(messages)
    await db.flush()

    await save_account_stickers(db, persona_id=chat.persona_id, channel_id=chat.channel_id,
                                stickers=[account(2)])

    gone, kept = await all_rows(db, select(Message).where(Message.chat_id == chat.id)
                                .order_by(Message.id))
    assert kept.sticker_id == two.id
    assert gone.sticker_id is None and gone.text == "😂"    # the message stays, unlinked


# --- the chat's catalog ----------------------------------------------------------------------


async def test_the_chat_offers_its_channels_stickers_favourites_first(
        db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    chat = await make_chat(db)
    late = await make_sticker(db, chat, position=1005)
    first_of_set = await make_sticker(db, chat, position=1000)
    second_fav = await make_sticker(db, chat, faved=True, position=3)
    first_fav = await make_sticker(db, chat, faved=True, position=0)
    other_account = await make_channel(db, await persona_of(db, chat))
    await make_sticker(db, chat, channel_id=other_account.id, faved=True)
    await make_sticker(db, await olya_chat(db), faved=True)       # another persona's

    rows = await chat_stickers(db, chat)
    assert [r.id for r in rows] == [first_fav.id, second_fav.id, first_of_set.id, late.id]

    monkeypatch.setattr(sticker_service, "MAX_CATALOG", 2)
    assert [r.id for r in await chat_stickers(db, chat)] == [first_fav.id, second_fav.id]


async def test_a_chat_without_her_stickers_has_none(db: AsyncSession) -> None:
    assert await chat_stickers(db, await make_chat(db)) == []


def test_the_block_names_each_sticker_by_id_emoji_and_set() -> None:
    rows = [
        PersonaSticker(id=17, emoji="😂", set_title="Утята", set_name="ducks"),
        PersonaSticker(id=18, emoji="", set_title="", set_name="cats"),
        PersonaSticker(id=19, emoji="🥰", set_title="", set_name=""),
    ]
    assert stickers_block(rows) == (
        f"{STICKERS_HEADING}\n- 17 😂 (Утята)\n- 18 · (cats)\n- 19 🥰 (избранное)")
    assert STICKERS_HEADING == "# Стикеры (можешь отправить)"
    assert stickers_block([]) == ""


def test_the_reference_is_the_document_and_its_set() -> None:
    row = PersonaSticker(doc_id=1, set_id=2, set_access_hash=3)
    assert ref_of(row) == StickerRef(doc_id=1, set_id=2, set_access_hash=3)


# --- the sticker action ----------------------------------------------------------------------


async def test_the_sticker_action_queues_a_pending_sticker_message(db: AsyncSession,
                                                                   deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    sticker = await make_sticker(db, chat, emoji="🥰")
    result = ApplyResult()
    await StickerHandler().apply(db, StickerAction(type="sticker", sticker_id=sticker.id), None,
                                 ctx, result)
    [message] = await messages_of(db, chat)
    assert (message.kind, message.text, message.status, message.direction, message.author) == (
        MessageKind.STICKER, "🥰", MessageStatus.PENDING, Direction.OUT, Author.PERSONA)
    assert message.sticker_id == sticker.id and message.media_id is None
    assert message.reply_run_id == ctx.reply_run_id
    assert result.message_ids == [message.id] and result.notes == []


async def test_a_gone_or_foreign_sticker_is_only_a_note(db: AsyncSession, deps: Deps) -> None:
    chat, ctx = await context(db, deps)
    foreign = await make_sticker(db, await olya_chat(db))          # another persona's
    result = ApplyResult()
    for sticker_id in (foreign.id, 999_999_999):
        await StickerHandler().apply(db, StickerAction(type="sticker", sticker_id=sticker_id),
                                     None, ctx, result)
    assert await messages_of(db, chat) == []
    assert result.message_ids == []
    assert result.notes == [f"стикер {foreign.id} пропал, не отправлен",
                            "стикер 999999999 пропал, не отправлен"]
