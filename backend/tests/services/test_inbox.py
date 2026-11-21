from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Channel, Chat, ChatEvent, Client, ClientIdentity, MediaAsset, Message
from sim.domain.enums import (
    Author,
    Direction,
    EventKind,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    UnderstandingStatus,
)
from sim.ports import InboundAttachment, InboundMessage, Peer
from sim.services.deps import Deps
from sim.services.inbox import Inbox
from tests.services.conftest import Fakes
from tests.services.factories import all_rows, fresh, make_channel, make_persona

PEER = Peer(external_id="555", access_hash="hash-1", name="Дима", username="dima",
            phone="+380000000000")


async def channel(db: AsyncSession) -> Channel:
    return await make_channel(db, await make_persona(db))


def inbound(channel_id: int, clock: FixedClock, external_id: str = "1", text: str = "привет",
            attachment: InboundAttachment | None = None) -> InboundMessage:
    return InboundMessage(channel_id=channel_id, peer=PEER, external_id=external_id, text=text,
                          sent_at=clock.now() - timedelta(seconds=1), attachment=attachment)


async def the_chat(db: AsyncSession) -> Chat:
    [chat] = await all_rows(db, select(Chat))
    return chat


async def test_first_contact_creates_client_with_ai_off(db: AsyncSession, deps: Deps,
                                                        fakes: Fakes, clock: FixedClock) -> None:
    ch = await channel(db)

    message_id = await Inbox(deps).ingest(inbound(ch.id, clock))

    assert message_id is not None
    [client] = await all_rows(db, select(Client))
    assert (client.name, client.username, client.phone, client.ai_enabled) == (
        "Дима", "dima", "+380000000000", False)
    assert client.last_seen_at == clock.now()
    [identity] = await all_rows(db, select(ClientIdentity))
    assert (identity.external_id, identity.access_hash) == ("555", "hash-1")
    chat = await the_chat(db)
    assert chat.ai_enabled and chat.unread_count == 1 and chat.channel_id == ch.id
    message = await db.get(Message, message_id)
    assert message is not None
    assert (message.direction, message.author, message.kind, message.status, message.text) == (
        Direction.IN, Author.CLIENT, MessageKind.TEXT, MessageStatus.RECEIVED, "привет")
    [event] = await all_rows(db, select(ChatEvent))
    assert (event.kind, event.message_id, event.in_context) == (EventKind.MESSAGE_IN,
                                                                message_id, True)
    assert fakes.tasks.calls == []          # AI is off for a new client


async def test_a_client_of_one_persona_writing_to_another(db: AsyncSession, deps: Deps,
                                                         fakes: Fakes,
                                                         clock: FixedClock) -> None:
    """The client stays the first persona's lead; the new chat is answered by the persona of
    the channel he wrote to — never by «the first persona»."""
    marina_channel = await channel(db)
    olya = await make_persona(db, name="Оля", reply_delay_s=3)
    olya_channel = await make_channel(db, olya)
    inbox = Inbox(deps)
    await inbox.ingest(inbound(marina_channel.id, clock, external_id="1"))
    [client] = await all_rows(db, select(Client))
    client.ai_enabled = True
    await db.flush()

    await inbox.ingest(inbound(olya_channel.id, clock, external_id="2"))

    assert client.persona_id == marina_channel.persona_id
    chats = {c.channel_id: c for c in await all_rows(db, select(Chat))}
    assert chats[olya_channel.id].persona_id == olya.id
    assert chats[marina_channel.id].persona_id == marina_channel.persona_id
    assert chats[olya_channel.id].client_id == client.id
    # Оля's own pause decides when she answers.
    assert fakes.tasks.calls == [("reply", chats[olya_channel.id].id, 3.0)]
    olya.is_active = False                              # archived: stored, not answered
    await db.flush()
    await inbox.ingest(inbound(olya_channel.id, clock, external_id="3"))
    assert len(fakes.tasks.calls) == 1


async def test_ingest_is_idempotent(db: AsyncSession, deps: Deps, clock: FixedClock) -> None:
    ch = await channel(db)
    inbox = Inbox(deps)
    first = await inbox.ingest(inbound(ch.id, clock, external_id="7"))
    second = await inbox.ingest(inbound(ch.id, clock, external_id="7"))
    assert first is not None and second is None
    assert len(await all_rows(db, select(Message))) == 1
    assert (await the_chat(db)).unread_count == 1
    assert len(await all_rows(db, select(Client))) == 1


async def test_reply_is_queued_when_ai_is_on(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    ch = await channel(db)
    inbox = Inbox(deps)
    await inbox.ingest(inbound(ch.id, clock, external_id="1"))
    [client] = await all_rows(db, select(Client))
    client.ai_enabled = True
    await db.flush()

    await inbox.ingest(inbound(ch.id, clock, external_id="2"))

    chat = await the_chat(db)
    assert fakes.tasks.calls == [("reply", chat.id, 8.0)]
    assert chat.unread_count == 2


async def test_attachment_goes_to_the_library_and_is_understood(db: AsyncSession, deps: Deps,
                                                                fakes: Fakes,
                                                                clock: FixedClock) -> None:
    ch = await channel(db)
    photo = InboundAttachment(kind="photo", data=b"JPEG", mime="image/jpeg", width=800,
                              height=600)

    message_id = await Inbox(deps).ingest(inbound(ch.id, clock, text="смотри",
                                                  attachment=photo))

    message = await db.get(Message, message_id)
    assert message is not None and message.kind == MessageKind.PHOTO
    media = await db.get(MediaAsset, message.media_id)
    assert media is not None
    assert (media.source, media.kind, media.persona_id, media.understanding) == (
        MediaSource.INCOMING, MediaKind.PHOTO, None, UnderstandingStatus.PENDING)
    assert (media.width, media.height) == (800, 600)
    assert fakes.storage.files[media.storage_key] == b"JPEG"
    assert fakes.tasks.calls == [("understand", media.id)]


async def test_animated_sticker_is_not_understood(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                  clock: FixedClock) -> None:
    ch = await channel(db)
    sticker = InboundAttachment(kind="sticker", data=b"TGS", mime="application/x-tgsticker")
    message_id = await Inbox(deps).ingest(inbound(ch.id, clock, text="", attachment=sticker))
    message = await db.get(Message, message_id)
    assert message is not None and message.kind == MessageKind.STICKER
    media = await db.get(MediaAsset, message.media_id)
    assert media is not None and media.understanding == UnderstandingStatus.SKIPPED
    assert fakes.tasks.calls == []


async def test_edit_and_delete_are_journaled(db: AsyncSession, deps: Deps,
                                             clock: FixedClock) -> None:
    ch = await channel(db)
    inbox = Inbox(deps)
    message_id = await inbox.ingest(inbound(ch.id, clock, external_id="9", text="привт"))

    await inbox.edited(ch.id, PEER, "9", "привет")
    await inbox.deleted(ch.id, ["9", "404"])
    await inbox.deleted(ch.id, ["9"])                         # repeated update: no second event

    message = await db.get(Message, message_id)
    assert message is not None and (await fresh(db, message)).text == "привет"
    events = await all_rows(db, select(ChatEvent).order_by(ChatEvent.id))
    assert [e.kind for e in events] == [EventKind.MESSAGE_IN, EventKind.MESSAGE_EDITED,
                                        EventKind.MESSAGE_DELETED]
    assert events[1].data == {"old": "привт", "new": "привет"}
    assert events[2].data["text"] == "привет" and events[2].data["direction"] == "in"


async def test_resolve_chat_reuses_the_client(db: AsyncSession, deps: Deps) -> None:
    ch = await channel(db)
    inbox = Inbox(deps)
    first = await inbox.resolve_chat(db, ch.id, PEER)
    second = await inbox.resolve_chat(db, ch.id, Peer(external_id="555", name="Другое имя"))
    assert first.id == second.id
    [client] = await all_rows(db, select(Client))
    assert client.name == "Дима"            # known data is never overwritten
