"""The persona's life against the database: the daily planner, the context blocks, canon from
the reply plan, busy replies, continuity references and the voice-note background."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import (
    Chat,
    Generation,
    LlmCall,
    Persona,
    PersonaArc,
    PersonaCanon,
    PersonaDay,
    ReplyRun,
)
from sim.domain.enums import (
    ArcStatus,
    CanonStatus,
    DaySource,
    EventKind,
    GenerationKind,
    GenerationStatus,
    LlmPurpose,
    RunStatus,
)
from sim.domain.life import DayEvent
from sim.domain.plan import GenerateSpec, VoiceAction
from sim.services.actions.base import ActionContext
from sim.services.actions.voice import VoiceHandler
from sim.services.context.builder import ContextBuilder
from sim.services.deps import Deps
from sim.services.generation import GenerationService, start_generation
from sim.services.life.planner import LifePlanner, Outcome, days_to_plan, needs_plan
from sim.services.reply import ReplyService
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_event,
    add_message,
    all_rows,
    fresh,
    make_chat,
    make_media,
    make_persona,
    ok_verdict,
)
from tests.services.life_seed import SATURDAY, SEED, TZ, day_plan, utc

LIFE_MODEL = "life-model"
FRIDAY = SATURDAY - timedelta(days=1)
SUNDAY = SATURDAY + timedelta(days=1)


async def life_persona(db: AsyncSession, **overrides: Any) -> Persona:
    fields: dict[str, Any] = {"timezone": TZ, "life": SEED["life"],
                             "models": {"life": LIFE_MODEL}}
    return await make_persona(db, **{**fields, **overrides})


async def add_day(db: AsyncSession, persona: Persona, day: date,
                  events: list[DayEvent] | None = None, **fields: Any) -> PersonaDay:
    row = PersonaDay(persona_id=persona.id, day=day, summary=fields.pop("summary", ""),
                     events=[e.model_dump(mode="json") for e in (events or [])],
                     source=fields.pop("source", DaySource.MODEL), model=LIFE_MODEL, **fields)
    db.add(row)
    await db.flush()
    return row


def plan_answer(*, arc_id: int | None = None, summary: str = "",
                events: list[DayEvent] | None = None) -> str:
    return json.dumps({
        "events": [e.model_dump(mode="json") for e in (events or day_plan())],
        "arc_updates": [{"arc_id": arc_id, "stage": "мастер начал снимать старую плитку",
                         "status": "active"}] if arc_id else [],
        "yesterday_summary": summary,
    }, ensure_ascii=False)


# --- the planner -------------------------------------------------------------------------------


async def test_planner_plans_a_day_moves_an_arc_and_sums_up_yesterday(
        db: AsyncSession, deps: Deps, fakes: Fakes) -> None:
    persona = await life_persona(db)
    arc = PersonaArc(persona_id=persona.id, title="Ремонт в ванной", summary="Меняют ванну",
                     stage="плитка в коридоре", status=ArcStatus.ACTIVE, started_on=FRIDAY)
    db.add(arc)
    await db.flush()
    await add_day(db, persona, FRIDAY, day_plan())
    fakes.llm.script(LIFE_MODEL, plan_answer(arc_id=arc.id, summary="Пятница была спокойной."))

    assert await LifePlanner(deps).plan(persona.id, SUNDAY) == Outcome.PLANNED

    [call] = fakes.llm.calls_for(LIFE_MODEL)
    prompt = call["messages"][1].parts[0].text
    assert "воскресенье, 22 ноября 2026, осень." in prompt
    assert "# ТВОЙ РАСПОРЯДОК" in prompt and f"- {arc.id}: Ремонт в ванной" in prompt
    assert "## пятница, 20 ноября 2026" in prompt
    # The planner's prompt (registry default) is the system message, placeholders filled.
    system = call["messages"][0].parts[0].text or ""
    assert "Марина" in system and "outfit" in system and "{" + "ambience}" not in system
    assert "2–3 предложения от третьего лица" in system and "20 ноября" in system
    assert (call["max_tokens"], call["key_tier"]) == (6000, "auto")
    assert call["json_schema"]["title"] == "DayPlan"
    [row] = await all_rows(db, select(PersonaDay).where(PersonaDay.day == SUNDAY))
    assert len(row.events) == 7 and row.source == DaySource.MODEL and row.model == LIFE_MODEL
    assert row.events[1]["outfit"] == "серый домашний свитер и тёмные леггинсы"
    assert (await fresh(db, arc)).stage == "мастер начал снимать старую плитку"
    friday = (await all_rows(db, select(PersonaDay).where(PersonaDay.day == FRIDAY)))[0]
    assert friday.summary == "Пятница была спокойной."
    [log] = await all_rows(db, select(LlmCall).where(LlmCall.purpose == LlmPurpose.LIFE))
    assert log.model == LIFE_MODEL
    # Planned once: no second call without `force`, a forced one replaces the events.
    assert await LifePlanner(deps).plan(persona.id, SUNDAY) == Outcome.EXISTS
    fakes.llm.script(LIFE_MODEL, plan_answer())
    assert await LifePlanner(deps).plan(persona.id, SUNDAY, force=True) == Outcome.PLANNED
    assert len(fakes.llm.calls_for(LIFE_MODEL)) == 2


async def test_planner_repairs_once_then_records_the_failure(db: AsyncSession, deps: Deps,
                                                             fakes: Fakes) -> None:
    persona = await life_persona(db)
    jump = day_plan()
    jump[2] = jump[2].model_copy(update={"place": "дома, кухня", "outfit": "красное платье"})
    fakes.llm.script(LIFE_MODEL, plan_answer(events=jump), plan_answer())
    assert await LifePlanner(deps).plan(persona.id, SATURDAY) == Outcome.PLANNED
    repair = fakes.llm.calls_for(LIFE_MODEL)[1]["messages"][-1].parts[0].text
    assert "одежда сменилась без причины" in repair

    fakes.llm.script(LIFE_MODEL, "not json", "still not json")
    assert await LifePlanner(deps).plan(persona.id, SUNDAY) == Outcome.FAILED
    [failed] = await all_rows(db, select(PersonaDay).where(PersonaDay.day == SUNDAY))
    assert failed.events == [] and "не прошёл проверку" in (failed.error or "")


async def test_planner_skips_without_a_model_and_keeps_edited_days(
        db: AsyncSession, deps: Deps) -> None:
    plain = await make_persona(db, name="Без жизни")
    assert await LifePlanner(deps).plan(plain.id, SATURDAY) == Outcome.SKIPPED
    persona = await life_persona(db)
    await add_day(db, persona, SATURDAY, source=DaySource.EDITED)
    assert await LifePlanner(deps).plan(persona.id, SATURDAY) == Outcome.EXISTS


def test_which_days_need_a_plan(clock: FixedClock) -> None:
    now = clock.now()                                   # Saturday 19:30 in Berlin
    assert days_to_plan(now, TZ, {}) == [SATURDAY, SUNDAY]
    planned = PersonaDay(day=SATURDAY, events=[{"start": "08:00"}], source=DaySource.MODEL)
    assert days_to_plan(now, TZ, {SATURDAY: planned}) == [SUNDAY]
    early = utc(SATURDAY, "02:00")
    assert days_to_plan(early, TZ, {}) == [SATURDAY]    # tomorrow only from 03:00
    failed = PersonaDay(day=SUNDAY, events=[], source=DaySource.MODEL, error="boom",
                        updated_at=now - timedelta(hours=1))
    assert not needs_plan(failed, now)
    assert needs_plan(PersonaDay(day=SUNDAY, events=[], source=DaySource.MODEL, error="boom",
                                 updated_at=now - timedelta(hours=4)), now)


async def test_tick_finds_the_days_to_plan(db: AsyncSession, deps: Deps) -> None:
    persona = await life_persona(db)
    await make_persona(db, name="Без планировщика")
    await add_day(db, persona, SATURDAY, day_plan())
    assert await LifePlanner(deps).due() == [(persona.id, SUNDAY)]


# --- context -----------------------------------------------------------------------------------


async def test_context_has_the_life_blocks_in_order(db: AsyncSession, clock: FixedClock,
                                                    deps: Deps) -> None:
    persona = await life_persona(db)
    chat = await make_chat(db, persona)
    await add_day(db, persona, SATURDAY, day_plan())
    db.add(PersonaCanon(persona_id=persona.id, fact="Соседскую собаку зовут Рекс",
                        status=CanonStatus.ACTIVE))
    db.add(PersonaCanon(persona_id=persona.id, fact="Отклонённый факт",
                        status=CanonStatus.REJECTED))
    await add_message(db, chat, "что делаешь?", at=clock.now())
    builder = ContextBuilder(clock, deps.settings)

    ctx = await builder.for_reply(db, chat.id)

    user = ctx.messages[1].parts[0].text or ""
    order = [user.index(h) for h in ("# Факты", "# ТВОЙ РАСПОРЯДОК",
                                     "# ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС", "# Канон о тебе",
                                     "# Собеседник", "# Лента")]
    assert order == sorted(order)
    assert "Ты сейчас: ужин и сериал" in user and "Рекс" in user
    assert "Отклонённый факт" not in user
    assert ctx.snapshot["now"].startswith("# ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС")
    assert ctx.snapshot["routine"].startswith("# ТВОЙ РАСПОРЯДОК")
    call = await builder.for_call(db, chat.id)
    assert "# ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС" in call.system


# --- reply: canon, busy delay, planning today --------------------------------------------------


def reply_with_canon(*facts: str) -> str:
    return json.dumps({"messages": [{"type": "text", "text": "у соседей пёс Рекс, громкий"}],
                       "remember": [], "canon": [{"fact": fact} for fact in facts]},
                      ensure_ascii=False)


async def test_reply_stores_new_canon_without_duplicates_and_asks_for_todays_plan(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await life_persona(db)
    chat = await make_chat(db, persona)
    db.add(PersonaCanon(persona_id=persona.id, fact="Любимое кафе — у Эльбы",
                        status=CanonStatus.REJECTED))
    await db.flush()
    _, trigger = await add_message(db, chat, "а соседи?", at=clock.now() - timedelta(minutes=1))
    fakes.llm.script("reply-model", reply_with_canon(
        "Соседскую собаку зовут Рекс", "  соседскую собаку зовут рекс. ",
        "любимое кафе — у эльбы"))
    fakes.llm.script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    rows = await all_rows(db, select(PersonaCanon).where(PersonaCanon.persona_id == persona.id)
                          .order_by(PersonaCanon.id))
    assert [(r.fact, r.status) for r in rows] == [
        ("Любимое кафе — у Эльбы", CanonStatus.REJECTED),
        ("Соседскую собаку зовут Рекс", CanonStatus.ACTIVE)]
    assert (rows[1].chat_id, rows[1].event_id) == (chat.id, trigger.id)
    assert fakes.tasks.of("plan_day") == [("plan_day", persona.id, "2026-11-21", False)]


async def test_reply_waits_while_she_is_asleep(db: AsyncSession, deps: Deps, fakes: Fakes,
                                               clock: FixedClock) -> None:
    clock.at = utc(SUNDAY, "02:00")
    persona = await life_persona(db, models={"life": ""})
    chat = await make_chat(db, persona)
    await add_message(db, chat, "спишь?", at=clock.now() - timedelta(seconds=30))

    result = await ReplyService(deps).run(chat.id)

    assert result.status is None and result.requeue_s is not None
    assert 4.5 * 3600 + 30 <= result.requeue_s <= 4.5 * 3600 + 300
    [(_, chat_id, delay)] = fakes.tasks.of("reply")
    assert chat_id == chat.id and delay == result.requeue_s
    assert fakes.tasks.of("mark_read") == []            # she has not read it


async def test_a_missed_call_is_answered_by_text_when_she_is_free(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await life_persona(db, models={"life": ""})
    chat = await make_chat(db, persona)
    await add_event(db, chat, EventKind.CALL, at=clock.now() - timedelta(minutes=5),
                    data={"status": "missed", "life_busy": "away"})
    fakes.llm.script("reply-model", reply_with_canon()).script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    assert fakes.tasks.of("mark_read") == []


# --- photos: scene and continuity ---------------------------------------------------------------


async def generate(db: AsyncSession, deps: Deps, chat: Chat, persona: Persona,
                   spec: GenerateSpec) -> Generation:
    generation = await start_generation(db, persona=persona, chat_id=chat.id, reply_run_id=None,
                                        kind=GenerationKind.PHOTO, spec=spec)
    assert await GenerationService(deps).run(generation.id) == GenerationStatus.DONE
    return await fresh(db, generation)


async def test_photos_of_one_event_share_the_scene_and_continuity_refs(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await life_persona(db, appearance="green eyes, long auburn hair")
    chat = await make_chat(db, persona)
    face = await make_media(db, persona.id, is_reference=True, storage=fakes.storage,
                            data=b"FACE")
    await add_day(db, persona, SATURDAY, day_plan())
    spec = GenerateSpec(prompt="улыбаюсь на диване", caption="вечер", tags=[])

    first = await generate(db, deps, chat, persona, spec)
    clock.advance(minutes=2)
    second = await generate(db, deps, chat, persona,
                            spec.model_copy(update={"prompt": "в красном платье на пляже"}))

    assert first.scene["event_id"] == second.scene["event_id"] == "2026-11-21T19:00"
    assert first.scene["outfit"] == "серый домашний свитер и тёмные леггинсы"
    assert first.ref_media_ids == [face.id]
    assert second.ref_media_ids[0] == face.id and len(second.ref_media_ids) == 2
    one, two = (call["prompt"] for call in fakes.image_gen.calls)
    assert "Her appearance (keep exactly): green eyes" in one
    assert "She wears: серый домашний свитер и тёмные леггинсы" in two
    assert "Reference image(s) 2–2: the same room and the same clothes" in two
    assert len(fakes.image_gen.calls[1]["references"]) == 2

    clock.at = utc(SATURDAY, "21:40")                   # the next event: a new scene
    third = await generate(db, deps, chat, persona, spec)
    assert third.scene["event_id"] == "2026-11-21T21:30" and third.ref_media_ids == [face.id]


# --- voice notes: the scene's background -------------------------------------------------------


async def test_voice_notes_of_one_event_share_the_background(db: AsyncSession, deps: Deps,
                                                             fakes: Fakes,
                                                             clock: FixedClock) -> None:
    persona = await life_persona(db)
    chat = await make_chat(db, persona)
    await add_day(db, persona, SATURDAY, day_plan())
    run = ReplyRun(chat_id=chat.id, status=RunStatus.RUNNING)
    db.add(run)
    await db.flush()
    ctx = ActionContext(deps=deps, persona=persona, chat_id=chat.id, reply_run_id=run.id)
    action = VoiceAction(type="voice", text="привет")

    await VoiceHandler().prepare(action, ctx)
    clock.advance(minutes=3)
    await VoiceHandler().prepare(action, ctx)
    clock.at = utc(SATURDAY, "12:00")                   # the market
    await VoiceHandler().prepare(action, ctx)

    first, second, market = [c for c in fakes.tools.calls if c[0] == "voice_note"]
    assert first == second and first[1] == "quiet_home"
    assert market[1] == "street" and market[2] != first[2]
    persona.voice_ambience_enabled = False
    await VoiceHandler().prepare(action, ctx)
    assert fakes.tools.calls[-1][0] == "wav_to_ogg_opus"
