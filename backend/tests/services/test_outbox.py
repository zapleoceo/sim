from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import ChatEvent
from sim.domain.enums import Author, EventKind, MessageKind, MessageStatus
from sim.services.outbox import enqueue_message, mark_failed, mark_sent
from tests.services.factories import all_rows, fresh, make_chat, make_media


async def test_enqueue_then_sent(db: AsyncSession, clock: FixedClock) -> None:
    chat = await make_chat(db)
    message = await enqueue_message(db, chat_id=chat.id, author=Author.OPERATOR,
                                    kind=MessageKind.TEXT, text="я тут")
    assert message.id is not None and message.status == MessageStatus.PENDING
    assert await all_rows(db, select(ChatEvent)) == []      # nothing happened in the chat yet

    await mark_sent(db, message, "tg-100", clock.now())

    assert (message.status, message.external_id, message.sent_at) == (
        MessageStatus.SENT, "tg-100", clock.now())
    assert (await fresh(db, chat)).last_message_at == clock.now()
    [event] = await all_rows(db, select(ChatEvent))
    assert (event.kind, event.actor, event.message_id, event.in_context) == (
        EventKind.MESSAGE_OUT, "operator", message.id, True)


async def test_failed_send_is_journaled_for_the_agent(db: AsyncSession) -> None:
    chat = await make_chat(db)
    photo = await make_media(db, chat.persona_id, caption="селфи")
    message = await enqueue_message(db, chat_id=chat.id, author=Author.PERSONA,
                                    kind=MessageKind.PHOTO, text="это я", media_id=photo.id)

    await mark_failed(db, message, "FLOOD_WAIT")

    assert message.status == MessageStatus.FAILED and message.error == "FLOOD_WAIT"
    [event] = await all_rows(db, select(ChatEvent))
    assert event.kind == EventKind.SEND_FAILED and event.in_context
    assert event.media_id == photo.id
    assert event.data == {"kind": "photo", "text": "это я", "error": "FLOOD_WAIT"}
