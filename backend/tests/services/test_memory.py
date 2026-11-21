from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.clock import FixedClock
from sim.config import Settings
from sim.db.models import Call, Chat, ChatEvent, Client
from sim.domain.enums import CallDirection, CallStatus, EventKind
from sim.services.client_memory import add_facts, normalize_fact, replace_facts
from sim.services.context.builder import ContextBuilder, load_events
from sim.services.deps import Deps
from sim.services.memory import MemoryService
from tests.services.conftest import Fakes, make_deps
from tests.services.factories import add_event, add_message, all_rows, fresh, make_chat

AT = datetime(2026, 11, 1, tzinfo=UTC)


def test_normalize_and_merge_facts() -> None:
    assert normalize_fact("  Живёт в Киеве. ") == "живет в киеве"
    memory = [{"fact": "Живёт в Киеве", "event_id": 1, "at": "x"}]
    merged = add_facts(memory, ["живёт в киеве!", "Есть дочь", "есть дочь", ""], event_id=5,
                       at=AT)
    assert [m["fact"] for m in merged] == ["Живёт в Киеве", "Есть дочь"]
    assert merged[1]["event_id"] == 5 and memory == [memory[0]]     # input untouched
    cleaned = replace_facts(merged, ["Есть дочь Аня", "живёт в Киеве"], event_id=9, at=AT)
    assert cleaned == [{"fact": "Есть дочь Аня", "event_id": 9, "at": AT.isoformat()},
                       {"fact": "живёт в Киеве", "event_id": 1, "at": "x"}]


def memory_answer(summary: str, facts: list[str]) -> str:
    return json.dumps({"summary": summary, "memory": facts}, ensure_ascii=False)


async def long_chat(db: AsyncSession, clock: FixedClock, count: int = 30) -> Chat:
    chat = await make_chat(db)
    start = clock.now() - timedelta(hours=count)
    for i in range(count):
        await add_message(db, chat, f"сообщение {i} " + "слово " * 60,
                          at=start + timedelta(hours=i))
    return chat


async def test_long_timeline_is_folded_into_the_summary(
    db: AsyncSession, fakes: Fakes, clock: FixedClock,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(env="test", context_budget_tokens=10, context_keep_recent_events=5)
    deps = make_deps(settings, sessions, clock, fakes)
    chat = await long_chat(db, clock)
    events = await load_events(db, chat.id, None)
    fakes.llm.script("memory-model", memory_answer("Дима писал много сообщений.",
                                                   ["Живёт в Киеве"]))

    assert await MemoryService(deps).refresh(chat.id)

    chat = await fresh(db, chat)
    assert chat.summary == "Дима писал много сообщений."
    assert chat.summary_until_event_id == events[-6].id
    client = await db.get(Client, chat.client_id)
    assert client is not None
    assert [m["fact"] for m in (await fresh(db, client)).memory] == ["Живёт в Киеве"]
    prompt = fakes.llm.calls_for("memory-model")[0]["messages"][1].parts[0].text or ""
    assert "# События для сводки" in prompt and "сообщение 0 " in prompt
    assert "сообщение 29 " not in prompt
    # Summary + timeline cover every event exactly once.
    ctx = await ContextBuilder(clock, settings).for_reply(db, chat.id)
    folded = [e.id for e in events if e.id <= (chat.summary_until_event_id or 0)]
    assert sorted(folded + ctx.event_ids) == [e.id for e in events]
    assert len(ctx.event_ids) == 5
    kinds = [e.kind for e in await all_rows(db, select(ChatEvent).where(
        ChatEvent.chat_id == chat.id))]
    assert kinds.count(EventKind.MEMORY_UPDATED) == 1


async def test_nothing_to_do_when_everything_fits(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                  clock: FixedClock) -> None:
    chat = await long_chat(db, clock, count=3)
    assert not await MemoryService(deps).refresh(chat.id)
    assert fakes.llm.calls == []


async def test_a_call_refreshes_the_memory(db: AsyncSession, deps: Deps, fakes: Fakes,
                                           clock: FixedClock) -> None:
    chat = await make_chat(db)
    call = Call(chat_id=chat.id, direction=CallDirection.IN, status=CallStatus.ENDED,
                duration_s=30, transcript=[{"who": "client", "text": "я врач", "t": 0}])
    db.add(call)
    await db.flush()
    await add_event(db, chat, EventKind.CALL, at=clock.now(), call_id=call.id)
    fakes.llm.script("memory-model", memory_answer("", ["Работает врачом"]))

    assert await MemoryService(deps).refresh(chat.id)

    chat = await fresh(db, chat)
    assert chat.summary_until_event_id is None and chat.summary == ""
    client = await db.get(Client, chat.client_id)
    assert client is not None
    assert [m["fact"] for m in (await fresh(db, client)).memory] == ["Работает врачом"]
    prompt = fakes.llm.calls_for("memory-model")[0]["messages"][1].parts[0].text or ""
    assert "# Новые звонки" in prompt and "я врач" in prompt
    # The same call is not processed twice.
    assert not await MemoryService(deps).refresh(chat.id)


async def test_empty_summary_does_not_move_the_boundary(
    db: AsyncSession, fakes: Fakes, clock: FixedClock,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(env="test", context_budget_tokens=10, context_keep_recent_events=5)
    deps = make_deps(settings, sessions, clock, fakes)
    chat = await long_chat(db, clock)
    fakes.llm.script("memory-model", memory_answer("", []))

    assert not await MemoryService(deps).refresh(chat.id)

    assert (await fresh(db, chat)).summary_until_event_id is None
