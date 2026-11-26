"""Her stickers on Telegram (sim/telegram/stickers.py): the account's favourites and installed
sets, a fresh document for sending, the sync on every connection and the `sync_stickers` task."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import types

from sim.db.models import PersonaSticker
from sim.ports import PortError, StickerRef
from sim.services.deps import reset_deps, set_deps
from sim.services.stickers import AccountSticker
from sim.telegram import stickers as tg_stickers
from sim.telegram import tasks as telegram_tasks
from sim.telegram.stickers import (
    StickerSync,
    account_stickers,
    emoji_of,
    set_of,
    sticker_media,
)
from tests.fakes.telegram import TestSessions
from tests.telegram.conftest import seed_world

DUCKS = SimpleNamespace(id=10, access_hash=11, short_name="ducks", title="Утята")
CATS = SimpleNamespace(id=20, access_hash=21, short_name="cats", title="Котики")


def sticker_doc(doc_id: int, *, alt: str = "", set_id: int = 0,
                access_hash: int = 0) -> SimpleNamespace:
    """A sticker Document as Telethon gives it: its sticker attribute names the emoji and set."""
    stickerset: Any = (types.InputStickerSetID(id=set_id, access_hash=access_hash) if set_id
                       else types.InputStickerSetEmpty())
    return SimpleNamespace(
        id=doc_id, access_hash=doc_id + 1, file_reference=f"ref{doc_id}".encode(),
        attributes=[types.DocumentAttributeImageSize(w=512, h=512),
                    types.DocumentAttributeSticker(alt=alt, stickerset=stickerset)])


class StickerClient:
    """A Telethon client answering the sticker requests by the request's type name."""

    def __init__(self, *, faved: list[Any] | None = None, sets: list[Any] | None = None,
                 full: dict[int, Any] | None = None, error: Exception | None = None) -> None:
        self.faved = faved or []
        self.sets = sets or []
        self.full = full or {}
        self.error = error
        self.requests: list[Any] = []

    def names(self) -> list[str]:
        return [type(request).__name__ for request in self.requests]

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        name = type(request).__name__
        if name == "GetFavedStickersRequest":
            return SimpleNamespace(stickers=self.faved)
        if name == "GetAllStickersRequest":
            return SimpleNamespace(sets=self.sets)
        if name == "GetStickerSetRequest":
            return self.full.get(request.stickerset.id, SimpleNamespace(packs=[], documents=[]))
        raise AssertionError(f"unexpected request {name}")


def account_client() -> StickerClient:
    """Two favourites (one has no set and is skipped), two installed sets: the ducks' first
    sticker takes its emoji from the set's pack, the cat has only the pack's emoji."""
    return StickerClient(
        faved=[sticker_doc(102, alt="🦆", set_id=10, access_hash=11),
               sticker_doc(999, alt="❓")],
        sets=[DUCKS, CATS],
        full={
            10: SimpleNamespace(packs=[SimpleNamespace(emoticon="🤣", documents=[101])],
                                documents=[sticker_doc(101, alt="😂", set_id=10, access_hash=11),
                                           sticker_doc(102, alt="🦆", set_id=10,
                                                       access_hash=11)]),
            20: SimpleNamespace(packs=[SimpleNamespace(emoticon="😺", documents=[201])],
                                documents=[sticker_doc(201, set_id=20, access_hash=21)]),
        })


async def stored(db: AsyncSession, channel_id: int) -> list[PersonaSticker]:
    result = await db.scalars(
        select(PersonaSticker).where(PersonaSticker.channel_id == channel_id)
        .order_by(PersonaSticker.faved.desc(), PersonaSticker.position)
        .execution_options(populate_existing=True))
    return list(result)


# --- reading the account ---------------------------------------------------------------------


def test_the_emoji_and_set_come_from_the_sticker_attribute():
    assert emoji_of(sticker_doc(1, alt="😂")) == "😂"
    assert emoji_of(sticker_doc(1)) == ""
    not_a_sticker = SimpleNamespace(attributes=[SimpleNamespace(alt="🙂")])
    assert emoji_of(not_a_sticker) == ""
    assert emoji_of(SimpleNamespace(attributes=None)) == ""
    assert emoji_of(SimpleNamespace()) == ""
    assert set_of(sticker_doc(1, set_id=10, access_hash=11)) == (10, 11)
    assert set_of(sticker_doc(1)) is None                     # no set: it can not be re-read
    assert set_of(SimpleNamespace(attributes=[])) is None


async def test_favourites_come_first_then_the_installed_sets_in_order():
    client = account_client()
    found = await account_stickers(client)
    assert found == [
        AccountSticker(doc_id=102, set_id=10, set_access_hash=11, emoji="🦆", faved=True,
                       position=0),
        AccountSticker(doc_id=101, set_id=10, set_access_hash=11, emoji="🤣", set_name="ducks",
                       set_title="Утята", position=1000),
        AccountSticker(doc_id=102, set_id=10, set_access_hash=11, emoji="🦆", set_name="ducks",
                       set_title="Утята", position=1001),
        AccountSticker(doc_id=201, set_id=20, set_access_hash=21, emoji="😺", set_name="cats",
                       set_title="Котики", position=2000),
    ]
    assert client.names() == ["GetFavedStickersRequest", "GetAllStickersRequest",
                              "GetStickerSetRequest", "GetStickerSetRequest"]
    first_set = client.requests[2].stickerset
    assert (first_set.id, first_set.access_hash) == (10, 11)


async def test_only_so_many_sets_and_stickers_per_set_are_read(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tg_stickers, "MAX_SETS", 1)
    monkeypatch.setattr(tg_stickers, "MAX_PER_SET", 1)
    client = account_client()
    found = await account_stickers(client)
    assert [(s.doc_id, s.faved) for s in found] == [(102, True), (101, False)]
    assert client.names().count("GetStickerSetRequest") == 1


async def test_an_account_without_stickers_has_none():
    assert await account_stickers(StickerClient()) == []


# --- sending -----------------------------------------------------------------------------------


async def test_the_sticker_is_sent_with_a_fresh_document_from_its_set():
    client = account_client()
    media = await sticker_media(client, StickerRef(doc_id=102, set_id=10, set_access_hash=11))
    assert isinstance(media, types.InputMediaDocument)
    assert isinstance(media.id, types.InputDocument)
    assert (media.id.id, media.id.access_hash, media.id.file_reference) == (102, 103, b"ref102")
    [request] = client.requests
    assert type(request).__name__ == "GetStickerSetRequest"
    assert (request.stickerset.id, request.stickerset.access_hash) == (10, 11)


@pytest.mark.parametrize("ref", [
    StickerRef(doc_id=555, set_id=10, set_access_hash=11),     # removed from its set
    StickerRef(doc_id=101, set_id=77, set_access_hash=1),      # the set is gone
])
async def test_a_sticker_that_is_gone_is_a_permanent_error(ref):
    with pytest.raises(PortError) as info:
        await sticker_media(account_client(), ref)
    assert not info.value.retryable


# --- the sync ----------------------------------------------------------------------------------


async def test_sync_stores_the_accounts_stickers_for_the_channels_persona(
        db: AsyncSession, sessions: TestSessions):
    world = await seed_world(db)
    assert await StickerSync(sessions).sync(world.channel.id, account_client()) == 3
    rows = await stored(db, world.channel.id)
    assert [(r.doc_id, r.emoji, r.faved, r.set_title, r.persona_id) for r in rows] == [
        (102, "🦆", True, "", world.persona.id),           # a favourite once, though in a set
        (101, "🤣", False, "Утята", world.persona.id),
        (201, "😺", False, "Котики", world.persona.id),
    ]


async def test_a_failed_read_keeps_what_was_stored_and_is_logged(
        db: AsyncSession, sessions: TestSessions, caplog: pytest.LogCaptureFixture):
    world = await seed_world(db)
    sync = StickerSync(sessions)
    await sync.sync(world.channel.id, account_client())
    with caplog.at_level("WARNING", logger="sim.telegram.stickers"):
        broken = StickerClient(error=ConnectionError("down"))
        assert await sync.sync(world.channel.id, broken) is None
    assert "stickers not read: ConnectionError" in caplog.text
    assert len(await stored(db, world.channel.id)) == 3


async def test_an_unknown_channel_stores_nothing(db: AsyncSession, sessions: TestSessions):
    world = await seed_world(db)
    assert await StickerSync(sessions).sync(999_999, account_client()) is None
    assert await stored(db, world.channel.id) == []


async def test_a_fresh_connection_reads_the_stickers_in_the_background(
        db: AsyncSession, sessions: TestSessions):
    world = await seed_world(db)
    sync = StickerSync(sessions)
    await sync.attach(world.channel.id, account_client())
    assert len(sync.tasks) == 1                         # the hook itself does not wait
    await asyncio.gather(*sync.tasks)
    await asyncio.sleep(0)
    assert not sync.tasks
    assert len(await stored(db, world.channel.id)) == 3


# --- telegram:sync_stickers --------------------------------------------------------------------


async def test_the_task_reads_the_account_through_the_runtime_of_the_process(
        db: AsyncSession, sessions: TestSessions, monkeypatch: pytest.MonkeyPatch):
    world = await seed_world(db)
    client = account_client()
    clients: list[int] = []

    def client_of(channel_id: int) -> StickerClient:
        clients.append(channel_id)
        return client

    monkeypatch.setitem(telegram_tasks._registry, "runtime", SimpleNamespace(client=client_of))
    set_deps(SimpleNamespace(sessions=sessions))  # type: ignore[arg-type]
    try:
        await telegram_tasks.sync_stickers.func(world.channel.id)
    finally:
        reset_deps()
    assert clients == [world.channel.id]
    assert len(await stored(db, world.channel.id)) == 3


async def test_the_task_waits_for_the_telegram_process(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delitem(telegram_tasks._registry, "runtime", raising=False)
    with pytest.raises(PortError) as info:
        await telegram_tasks.sync_stickers.func(5)
    assert info.value.retryable


def test_the_task_has_the_name_the_queue_defers():
    from sim.tasks.queue import SYNC_STICKERS_TASK

    assert telegram_tasks.SYNC_STICKERS_TASK == SYNC_STICKERS_TASK == "telegram:sync_stickers"
    assert telegram_tasks.sync_stickers.queue == telegram_tasks.QUEUE == "telegram"
