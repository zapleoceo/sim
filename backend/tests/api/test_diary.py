"""The lead card's «Дни» (docs/api.md, docs/memory.md): a chat's day and week summaries, the
owner's edit (the night job leaves it alone), «Пересобрать день», «Обновить паспорт сейчас»."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import ChatDay, ChatWeek, Persona
from sim.domain.enums import DaySource
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World


def days_path(world: World, tail: str = "") -> str:
    return f"/api/chats/{world.chat_id}/days{tail}"


async def test_days_and_weeks_newest_first(api: ApiClient, world: World,
                                           db: AsyncSession) -> None:
    for day, summary in ((date(2026, 11, 19), "Четверг."), (date(2026, 11, 20), "Пятница.")):
        db.add(ChatDay(chat_id=world.chat_id, day=day, summary=summary, facts=["врач"],
                       source=DaySource.MODEL, model="codex-sub/gpt-6.1-sol"))
    db.add(ChatWeek(chat_id=world.chat_id, week=date(2026, 11, 9), summary="Неделя.",
                    facts=[], source=DaySource.MODEL, model="m"))
    await db.commit()

    body = (await api.get(days_path(world))).json()

    assert body["today"] == "2026-11-21"
    assert [(d["day"], d["summary"], d["facts"], d["source"]) for d in body["items"]] == [
        ("2026-11-20", "Пятница.", ["врач"], "model"),
        ("2026-11-19", "Четверг.", ["врач"], "model")]
    assert [(w["week"], w["summary"]) for w in body["weeks"]] == [("2026-11-09", "Неделя.")]
    assert len((await api.get(days_path(world), params={"limit": 1})).json()["items"]) == 1
    assert (await api.get("/api/chats/999999/days")).status_code == 404


async def test_an_edit_marks_the_day_edited(api: ApiClient, world: World,
                                            db: AsyncSession) -> None:
    saved = await api.put(days_path(world, "/2026-11-20"), json={
        "summary": "  Дима рассказал о работе. ", "facts": ["врач", " Врач. ", ""]})

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["day"], body["summary"], body["facts"], body["source"]) == (
        "2026-11-20", "Дима рассказал о работе.", ["врач"], "edited")
    [row] = (await db.scalars(select(ChatDay).where(ChatDay.chat_id == world.chat_id))).all()
    assert row.source == DaySource.EDITED
    again = await api.put(days_path(world, "/2026-11-20"), json={"summary": "Иначе."})
    assert again.json()["summary"] == "Иначе." and again.json()["facts"] == []
    assert (await api.put(days_path(world, "/2026-11-20"),
                          json={"summary": "   "})).status_code == 400
    assert (await api.put(days_path(world, "/2026-11-20"),
                          json={"summary": "x", "extra": 1})).status_code == 400


async def test_rebuild_and_refresh_queue_tasks(api: ApiClient, world: World, deps: FakeDeps,
                                               db: AsyncSession) -> None:
    queued = await api.post(days_path(world, "/2026-11-20/rebuild"))
    assert queued.status_code == 202 and queued.json() == {"queued": True, "day": "2026-11-20"}
    unfinished = await api.post(days_path(world, "/2026-11-21/rebuild"))
    assert unfinished.status_code == 400 and "не закончился" in unfinished.json()["error"]
    refreshed = await api.post(f"/api/chats/{world.chat_id}/passport/refresh")
    assert refreshed.status_code == 202
    assert [c[:2] for c in deps.tasks.calls] == [
        ("summarize_day", (world.chat_id, "2026-11-20", True)),
        ("update_passport", (world.chat_id, 0))]

    persona = await db.get(Persona, world.persona_id)
    assert persona is not None
    persona.agents = {**persona.agents, "memory": {"model": ""}, "passport": {"model": ""}}
    await db.commit()
    off = await api.post(days_path(world, "/2026-11-20/rebuild"))
    assert off.status_code == 400 and "Летописец" in off.json()["error"]
    no_writer = await api.post(f"/api/chats/{world.chat_id}/passport/refresh")
    assert no_writer.status_code == 400 and "Паспортистка" in no_writer.json()["error"]
