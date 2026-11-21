from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.config import Settings
from sim.db.models import Call, Client
from sim.domain.enums import CallDirection, CallStatus, Direction, EventKind, MessageKind
from sim.services.context.builder import ContextBuilder
from tests.services.factories import add_event, add_message, make_chat, make_media


async def test_reply_context_blocks_and_determinism(db: AsyncSession, clock: FixedClock,
                                                    settings: Settings) -> None:
    chat = await make_chat(db)
    client = await db.get(Client, chat.client_id)
    assert client is not None
    client.notes = "любит кофе"
    client.memory = [{"fact": "Живёт в Киеве", "event_id": None, "at": "2026-11-01"}]
    photo = await make_media(db, chat.persona_id, caption="селфи у окна", tags=["селфи"])
    sent = await make_media(db, chat.persona_id, caption="на пляже")
    await add_message(db, chat, "привет", at=clock.now() - timedelta(hours=1))
    await add_message(db, chat, "", at=clock.now() - timedelta(minutes=50),
                      direction=Direction.OUT, kind=MessageKind.PHOTO, media=sent)
    await add_message(db, chat, "как дела?", at=clock.now() - timedelta(minutes=3))
    builder = ContextBuilder(clock, settings)

    ctx = await builder.for_reply(db, chat.id)
    again = await builder.for_reply(db, chat.id)

    assert ctx.messages == again.messages and ctx.snapshot == again.snapshot
    system = ctx.messages[0].parts[0].text or ""
    user = ctx.messages[1].parts[0].text or ""
    assert system.startswith("Ты Марина") and "# Как отвечать" in system
    for block in ("# Факты", "# Собеседник", "# Лента", "# Каталог медиа"):
        assert block in user
    assert "суббота, 21 ноября 2026, 20:30" in user
    assert "Заметки оператора: любит кофе" in user and "- Живёт в Киеве" in user
    assert "[21.11 20:27] Дима: как дела?" in user
    assert f"- id {photo.id} · фото · селфи у окна · теги: селфи" in user
    assert f"id {sent.id} " not in user
    assert "фото «на пляже» — 21.11 19:40" in user
    assert ctx.catalog == {photo.id: photo.kind}
    assert ctx.client_lines == ["[21.11 19:30] Дима: привет", "[21.11 20:27] Дима: как дела?"]
    assert ctx.snapshot["event_ids"] == ctx.event_ids and len(ctx.event_ids) == 3
    assert not ctx.needs_compression


async def test_summary_boundary_hides_older_events(db: AsyncSession, clock: FixedClock,
                                                   settings: Settings) -> None:
    chat = await make_chat(db)
    _, old = await add_message(db, chat, "старое", at=clock.now() - timedelta(days=2))
    await add_message(db, chat, "новое", at=clock.now())
    chat.summary = "Познакомились позавчера."
    chat.summary_until_event_id = old.id
    await db.flush()

    ctx = await ContextBuilder(clock, settings).for_reply(db, chat.id)

    user = ctx.messages[1].parts[0].text or ""
    assert "# Сводка прошлой переписки\nПознакомились позавчера." in user
    assert "старое" not in user and "новое" in user
    assert old.id not in ctx.event_ids


async def test_call_context(db: AsyncSession, clock: FixedClock, settings: Settings) -> None:
    chat = await make_chat(db)
    call = Call(chat_id=chat.id, direction=CallDirection.IN, status=CallStatus.ENDED,
                duration_s=65, transcript=[{"who": "client", "text": "алло", "t": 0}])
    db.add(call)
    await db.flush()
    await add_event(db, chat, EventKind.CALL, at=clock.now(), call_id=call.id)

    ctx = await ContextBuilder(clock, settings).for_call(db, chat.id)

    assert ctx.greeting == "Алло?" and ctx.language == "ru"
    assert ctx.voice == "Vindemiatrix" and ctx.model == "live-model"
    assert ctx.max_seconds == settings.call_max_seconds
    assert "Сейчас ты говоришь голосом" in ctx.system and "# Как отвечать" not in ctx.system
    assert "[звонок входящий, 1:05]\n  Дима: алло" in ctx.system
    assert "звонков: 1" in ctx.system
