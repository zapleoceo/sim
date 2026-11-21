"""«А помнишь…?» (docs/memory.md): what code looks up for a `recall` (dates resolved by code,
a topic by Russian full-text search) and the reply's second pass with the «Вспомнила» block."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Chat, ChatDay, LlmCall, ReplyRun
from sim.domain.enums import DaySource, Direction, LlmPurpose, RunStatus
from sim.domain.plan import RecallSpec
from sim.services.context.render import Speakers
from sim.services.deps import Deps
from sim.services.memory.recall import HEADING, recollect, search_days
from sim.services.reply import ReplyService
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_message,
    all_rows,
    make_chat,
    ok_verdict,
    plan_json,
)

KYIV = "Europe/Kyiv"
THURSDAY = date(2026, 11, 19)           # «позавчера»: the clock is Saturday 21.11
WHO = Speakers(client="Дима", persona="Марина", tz=KYIV)


def at(day: date, hour: int) -> datetime:
    """`hour` o'clock in Kyiv (UTC+2 in November)."""
    return datetime(day.year, day.month, day.day, hour - 2, tzinfo=UTC)


async def history(db: AsyncSession) -> Chat:
    chat = await make_chat(db)
    await add_message(db, chat, "Мою собаку зовут Бублик", at=at(date(2026, 11, 12), 19))
    await add_message(db, chat, "я сегодня в больнице дежурю", at=at(THURSDAY, 20))
    await add_message(db, chat, "держись!", at=at(THURSDAY, 21), direction=Direction.OUT)
    db.add(ChatDay(chat_id=chat.id, day=THURSDAY, summary="Дима дежурил в больнице.",
                   facts=["врач"], source=DaySource.MODEL, model="m"))
    db.add(ChatDay(chat_id=chat.id, day=date(2026, 11, 5), summary="Говорили про собаку.",
                   facts=[], source=DaySource.MODEL, model="m"))
    await db.flush()
    return chat


async def test_dates_give_the_day_summary_and_its_timeline(db: AsyncSession,
                                                           clock: FixedClock) -> None:
    chat = await history(db)

    found = await recollect(db, chat.id, RecallSpec(date_from="позавчера"), WHO, clock.now())

    assert found.days == [THURSDAY]
    assert found.request["resolved"] == {"first": "2026-11-19", "last": "2026-11-19"}
    assert found.text.startswith(HEADING)
    assert "Ты вспоминала четверг, 19 ноября 2026." in found.text
    assert ("## четверг, 19 ноября 2026\nИтог дня: Дима дежурил в больнице.\nПереписка:\n"
            "[19.11 20:00] Дима: я сегодня в больнице дежурю\n"
            "[19.11 21:00] Марина: держись!") in found.text
    nothing = await recollect(db, chat.id, RecallSpec(date_from="в прошлый понедельник"), WHO,
                              clock.now())
    assert nothing.days == [date(2026, 11, 16)]
    assert "Ничего не вспомнилось" in nothing.text
    unknown = await recollect(db, chat.id, RecallSpec(date_from="когда-то"), WHO, clock.now())
    assert unknown.days == [] and "Не получилось понять, какой это день" in unknown.text


async def test_a_topic_is_found_by_full_text_search(db: AsyncSession,
                                                    clock: FixedClock) -> None:
    chat = await history(db)

    # Russian stemming: «собака» finds «собаку» in a message and in a day summary.
    assert await search_days(db, chat.id, "собака", KYIV, date(2026, 11, 21)) == [
        date(2026, 11, 5), date(2026, 11, 12)]
    assert await search_days(db, chat.id, "больница", KYIV, date(2026, 11, 21)) == [THURSDAY]
    assert await search_days(db, chat.id, "больница", KYIV, THURSDAY) == []    # not today
    found = await recollect(db, chat.id, RecallSpec(query="собака"), WHO, clock.now())
    assert found.days == [date(2026, 11, 5), date(2026, 11, 12)]
    assert "Итог дня: Говорили про собаку." in found.text
    assert "Дима: Мою собаку зовут Бублик" in found.text
    assert "Ты вспоминала, когда речь шла о «собака»." in found.text


async def test_the_reply_remembers_once_then_answers(db: AsyncSession, deps: Deps,
                                                     fakes: Fakes, clock: FixedClock) -> None:
    chat = await history(db)
    await add_message(db, chat, "а помнишь, что я делал позавчера?",
                      at=clock.now() - timedelta(minutes=1))
    recall = json.dumps({"messages": [], "recall": {"date_from": "позавчера"}},
                        ensure_ascii=False)
    fakes.llm.script("reply-model", recall,
                     recall,                               # a second recall is refused
                     plan_json({"type": "text", "text": "ты дежурил в больнице!"}))
    fakes.llm.script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    calls = fakes.llm.calls_for("reply-model")
    assert len(calls) == 3
    second = calls[1]["messages"]
    assert second[-1].parts[0].text.startswith(HEADING)
    assert "Дима дежурил в больнице." in (second[-1].parts[0].text or "")
    assert "уже вспоминала" in (calls[2]["messages"][-1].parts[0].text or "")
    logs = await all_rows(db, select(LlmCall).where(LlmCall.purpose == LlmPurpose.REPLY)
                          .order_by(LlmCall.id))
    assert "recall" not in logs[0].request
    assert logs[1].request["recall"]["resolved"]["first"] == "2026-11-19"
    [run] = await all_rows(db, select(ReplyRun).where(ReplyRun.chat_id == chat.id))
    assert run.context["recall"]["days"] == ["2026-11-19"]
    assert run.context["recall_text"].startswith(HEADING)
    # «печатает» is shown again for the second call.
    assert fakes.tasks.actions()[:2] == ["typing", "typing"]
