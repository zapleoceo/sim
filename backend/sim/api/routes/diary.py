"""`/api/chats/{id}/days`, `/passport/refresh`: a chat's day memory on the lead card, «Дни»
(docs/memory.md, docs/api.md). The owner reads and edits the chronicler's day summaries (an
edited day is never overwritten by the night job), rebuilds a finished day and refreshes the
client's passport now; the last two are tasks, the page re-reads the list."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from litestar import get, post, put
from litestar.exceptions import ClientException
from litestar.params import Parameter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.queries import get_or_404
from sim.api.schemas import ChatDayIn
from sim.db.models import Chat, ChatDay, ChatWeek, Persona
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import DaySource
from sim.domain.memory import clean_facts
from sim.services.context.window import local_day

Json = dict[str, Any]
DAYS_LIMIT = 120
WEEKS_LIMIT = 12


async def chat_and_persona(db: AsyncSession, chat_id: int) -> tuple[Chat, Persona]:
    chat = await get_or_404(db, Chat, chat_id, "Чат")
    persona = await get_or_404(db, Persona, chat.persona_id, "Персонаж")
    return chat, persona


@get("/chats/{chat_id:int}/days")
async def list_days(chat_id: int, ctx: ApiContext, db: AsyncSession,
                    limit: Annotated[int, Parameter(ge=1, le=DAYS_LIMIT)] = 60) -> Json:
    """Newest first: the day summaries and the week rollups of the chat."""
    chat, persona = await chat_and_persona(db, chat_id)
    days = await db.scalars(select(ChatDay).where(ChatDay.chat_id == chat.id)
                            .order_by(ChatDay.day.desc()).limit(limit))
    weeks = await db.scalars(select(ChatWeek).where(ChatWeek.chat_id == chat.id)
                             .order_by(ChatWeek.week.desc()).limit(WEEKS_LIMIT))
    return {"today": local_day(ctx.deps.clock.now(), persona.timezone),
            "items": [views.diary_view(row) for row in days],
            "weeks": [views.diary_view(row) for row in weeks]}


@put("/chats/{chat_id:int}/days/{day:date}")
async def update_day(chat_id: int, day: date, data: ChatDayIn, db: AsyncSession) -> Json:
    """The owner's edit: the day is marked `edited` and the night job leaves it alone."""
    chat, _ = await chat_and_persona(db, chat_id)
    summary = data.summary.strip()
    if not summary:
        raise ClientException(detail="Итог дня не может быть пустым")
    row = await db.scalar(select(ChatDay).where(ChatDay.chat_id == chat.id,
                                                ChatDay.day == day))
    if row is None:
        row = ChatDay(chat_id=chat.id, day=day, model="")
        db.add(row)
    row.summary = summary
    row.facts = clean_facts(data.facts)
    row.source = DaySource.EDITED
    row.error = None
    await db.flush()
    return views.diary_view(row)


@post("/chats/{chat_id:int}/days/{day:date}/rebuild", status_code=202)
async def rebuild_day(chat_id: int, day: date, ctx: ApiContext, db: AsyncSession) -> Json:
    """«Пересобрать день»: the chronicler writes the day again (an edit is overwritten), then
    the passport is updated from it."""
    chat, persona = await chat_and_persona(db, chat_id)
    if not agent_config(persona, AgentKey.MEMORY).model:
        raise ClientException(detail="Летописец выключен: выберите ему модель в «Агентах»")
    if day >= local_day(ctx.deps.clock.now(), persona.timezone):
        raise ClientException(detail="День ещё не закончился: итог пишется после полуночи")
    await ctx.deps.tasks.summarize_day(chat.id, day.isoformat(), True)
    return {"queued": True, "day": day}


@post("/chats/{chat_id:int}/passport/refresh", status_code=202)
async def refresh_passport(chat_id: int, ctx: ApiContext, db: AsyncSession) -> Json:
    """«Обновить паспорт сейчас»: the passport writer reads today of this chat now."""
    chat, persona = await chat_and_persona(db, chat_id)
    if not agent_config(persona, AgentKey.PASSPORT).model:
        raise ClientException(detail="Паспортистка выключена: выберите ей модель в «Агентах»")
    await ctx.deps.tasks.update_passport(chat.id, 0)
    return {"queued": True}


ROUTES = [list_days, update_day, rebuild_day, refresh_passport]
