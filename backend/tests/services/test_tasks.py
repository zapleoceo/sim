from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import procrastinate
import pytest
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.testing import InMemoryConnector
from sqlalchemy.ext.asyncio import AsyncSession

from sim.call_scope import current_scope
from sim.clock import FixedClock
from sim.config import Settings
from sim.db.models import Client
from sim.services.attention.schedule import request_check
from sim.services.deps import Deps, get_deps, reset_deps, set_deps
from sim.services.voices import GEMINI_VOICES, VOICE_IDS, is_voice
from sim.tasks import agent
from sim.tasks.app import conninfo, make_app
from sim.tasks.queue import (
    ATTENTION_TICK_TASK,
    CHAT_ACTION_TASK,
    GENERATE_TASK,
    LIFE_TICK_TASK,
    MARK_READ_TASK,
    MEMORY_TASK,
    MEMORY_TICK_TASK,
    NIGHT_TASK,
    PASSPORT_TASK,
    PHONE_CHECK_TASK,
    PLAN_DAY_TASK,
    REPLY_TASK,
    SEND_TASK,
    SUMMARIZE_DAY_TASK,
    UNDERSTAND_TASK,
    ProcrastinateQueue,
)
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_message,
    make_chat,
    make_persona,
    ok_verdict,
    plan_json,
)


class _Deferrer:
    def __init__(self, app: FakeApp, name: str, options: dict[str, Any]) -> None:
        self.app, self.name, self.options = app, name, options

    async def defer_async(self, **kwargs: Any) -> int:
        lock = self.options.get("queueing_lock")
        if lock is not None and lock in self.app.waiting:
            raise AlreadyEnqueued("already enqueued")
        if lock is not None:
            self.app.waiting.add(lock)
        self.app.deferred.append((self.name, kwargs, self.options))
        return len(self.app.deferred)


class FakeApp:
    """Only what ProcrastinateQueue uses: configure_task(name, **options).defer_async()."""

    def __init__(self) -> None:
        self.deferred: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.waiting: set[str] = set()

    def configure_task(self, name: str, **options: Any) -> _Deferrer:
        return _Deferrer(self, name, options)


async def test_queue_defers_by_name_with_locks() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app)  # type: ignore[arg-type]

    await queue.reply(7, delay_s=8)
    await queue.reply(7)                    # absorbed by the waiting one
    await queue.understand(3)
    await queue.generate(4)
    await queue.send(5, chat_id=7)
    await queue.send(6)
    await queue.refresh_memory(7)
    await queue.mark_read(9)
    await queue.mark_read(9)                # one receipt per message is enough
    await queue.chat_action(7, "typing", 40)
    await queue.chat_action(7, "cancel", 0)  # never absorbed by the waiting typing

    assert app.deferred == [
        (REPLY_TASK, {"chat_id": 7}, {"queue": "agent", "lock": "chat:7",
                                      "queueing_lock": "reply:7", "schedule_in": {"seconds": 8}}),
        (UNDERSTAND_TASK, {"media_id": 3}, {"queue": "agent", "queueing_lock": "understand:3"}),
        (GENERATE_TASK, {"generation_id": 4}, {"queue": "agent",
                                               "queueing_lock": "generation:4"}),
        (SEND_TASK, {"message_id": 5}, {"queue": "telegram", "lock": "send:7",
                                        "queueing_lock": "send-message:5"}),
        (SEND_TASK, {"message_id": 6}, {"queue": "telegram", "queueing_lock": "send-message:6"}),
        (MEMORY_TASK, {"chat_id": 7}, {"queue": "agent", "lock": "chat:7",
                                       "queueing_lock": "memory:7"}),
        (MARK_READ_TASK, {"message_id": 9}, {"queue": "telegram",
                                             "queueing_lock": "mark-read:9"}),
        (CHAT_ACTION_TASK, {"chat_id": 7, "action": "typing", "seconds": 40.0},
         {"queue": "telegram", "lock": "chat-action:7"}),
        (CHAT_ACTION_TASK, {"chat_id": 7, "action": "cancel", "seconds": 0.0},
         {"queue": "telegram", "lock": "chat-action:7"}),
    ]


def test_conninfo_strips_the_sqlalchemy_driver() -> None:
    assert conninfo("postgresql+psycopg://u:p@db:5432/sim") == "postgresql://u:p@db:5432/sim"
    assert conninfo("postgresql://u:p@db/sim") == "postgresql://u:p@db/sim"


def test_make_app_registers_agent_tasks_under_shared_names() -> None:
    app = make_app(Settings(env="test"))
    assert {REPLY_TASK, UNDERSTAND_TASK, GENERATE_TASK, MEMORY_TASK} <= set(app.tasks)
    assert app.tasks[REPLY_TASK].queue == "agent"
    assert {SEND_TASK, MARK_READ_TASK, CHAT_ACTION_TASK} <= set(app.tasks)
    assert app.tasks[CHAT_ACTION_TASK].queue == "telegram"


def test_deps_registry(deps: Deps) -> None:
    reset_deps()
    with pytest.raises(RuntimeError):
        get_deps()
    set_deps(deps)
    assert get_deps() is deps
    reset_deps()


def test_voices_list() -> None:
    assert len(GEMINI_VOICES) == 30 and len(VOICE_IDS) == 30
    assert is_voice("Vindemiatrix") and is_voice("Kore") and not is_voice("Alice")


async def test_reply_task_runs_the_service(db: AsyncSession, deps: Deps, fakes: Fakes,
                                           clock: FixedClock) -> None:
    chat = await make_chat(db)
    await add_message(db, chat, "привет", at=clock.now().replace(hour=17))
    fakes.llm.script("reply-model", plan_json({"type": "text", "text": "хай"}))
    fakes.llm.script("guard-model", ok_verdict())
    set_deps(deps)
    try:
        await agent.reply_chat(chat.id)
        await agent.refresh_memory(chat.id)            # fits the budget: nothing to do
        await agent.understand_media(999_999)          # unknown media: nothing to do
        await agent.run_generation(999_999)
    finally:
        reset_deps()
    assert [call[0] for call in fakes.tasks.calls] == ["mark_read", "chat_action", "send"]


async def test_broker_tasks_run_in_the_scope_of_their_job() -> None:
    """Every attempt of one Procrastinate job sends the same Broker keys (docs/broker.md §2b)."""
    seen: list[str | None] = []

    async def work(chat_id: int) -> None:
        scope = current_scope()
        seen.append(f"{scope.name}:{chat_id}" if scope is not None else None)

    task = agent.in_job_scope(work)
    await task(SimpleNamespace(job=SimpleNamespace(id=42)), chat_id=7)
    await task(SimpleNamespace(job=SimpleNamespace(id=42)), chat_id=7)     # its retry
    await task(SimpleNamespace(job=SimpleNamespace(id=43)), chat_id=7)     # a new job
    assert seen == ["job-42:7", "job-42:7", "job-43:7"]
    assert current_scope() is None
    assert task.__name__ == "work"


def test_broker_tasks_are_registered_with_the_job_context() -> None:
    # A bare app: `make_app` takes the Telegram blueprint, which a process can add only once.
    app = procrastinate.App(connector=InMemoryConnector())
    agent.register_agent_tasks(app)
    broker_tasks = {REPLY_TASK, UNDERSTAND_TASK, GENERATE_TASK, MEMORY_TASK, PASSPORT_TASK,
                    PLAN_DAY_TASK, PHONE_CHECK_TASK, NIGHT_TASK, SUMMARIZE_DAY_TASK}
    assert all(app.tasks[name].pass_context for name in broker_tasks)
    assert not app.tasks[LIFE_TICK_TASK].pass_context
    assert app.tasks[REPLY_TASK].retry_strategy is not None
    assert app.tasks[GENERATE_TASK].retry_strategy is None


async def test_passport_queue_and_registration() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app)  # type: ignore[arg-type]
    await queue.update_passport(7, 5)
    await queue.update_passport(7, 5)                   # one waiting update per chat
    await queue.update_passport(8)
    assert app.deferred == [
        (PASSPORT_TASK, {"chat_id": 7}, {"queue": "agent", "lock": "passport:7",
                                         "queueing_lock": "passport:7",
                                         "schedule_in": {"seconds": 5}}),
        (PASSPORT_TASK, {"chat_id": 8}, {"queue": "agent", "lock": "passport:8",
                                         "queueing_lock": "passport:8"}),
    ]
    real = make_app(Settings(env="test"))
    assert real.tasks[PASSPORT_TASK].queue == "agent"


async def test_passport_task_runs_the_service(db: AsyncSession, deps: Deps, fakes: Fakes,
                                              clock: FixedClock) -> None:
    chat = await make_chat(db)
    await add_message(db, chat, "я врач", at=clock.now())
    fakes.llm.script("passport-model", '{"text": "Жизнь: врач", "occupation": "врач"}')
    set_deps(deps)
    try:
        await agent.update_passport(chat.id)
        await agent.update_passport(999_999)            # unknown chat: nothing to do
    finally:
        reset_deps()
    client = await db.get(Client, chat.client_id)
    assert client is not None
    await db.refresh(client)
    assert client.passport["occupation"] == "врач"


async def test_life_tick_plans_every_active_persona(db: AsyncSession, deps: Deps,
                                                    fakes: Fakes) -> None:
    marina = await make_persona(db, models={"life": "life-model"}, timezone="Europe/Berlin")
    olya = await make_persona(db, name="Оля", models={"life": "life-model"},
                              timezone="Europe/Berlin")
    await make_persona(db, name="Архив", models={"life": "life-model"}, is_active=False)
    set_deps(deps)
    try:
        await agent.life_tick(0)
    finally:
        reset_deps()
    planned = {call[1] for call in fakes.tasks.of("plan_day")}
    assert planned == {marina.id, olya.id}


async def test_phone_checks_are_never_absorbed_and_are_registered() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app)  # type: ignore[arg-type]
    await queue.phone_check(3, 600)
    await queue.phone_check(3, 20)                    # earlier: must not wait or be absorbed
    assert app.deferred == [
        (PHONE_CHECK_TASK, {"persona_id": 3}, {"queue": "agent",
                                               "schedule_in": {"seconds": 600}}),
        (PHONE_CHECK_TASK, {"persona_id": 3}, {"queue": "agent",
                                               "schedule_in": {"seconds": 20}}),
    ]
    real = make_app(Settings(env="test"))
    assert {PHONE_CHECK_TASK, ATTENTION_TICK_TASK} <= set(real.tasks)
    assert real.tasks[PHONE_CHECK_TASK].queue == "agent"
    assert any(ATTENTION_TICK_TASK in str(key)
               for key in real.periodic_registry.periodic_tasks)


async def test_attention_tasks_run_the_check_and_the_sweep(db: AsyncSession, deps: Deps,
                                                           fakes: Fakes,
                                                           clock: FixedClock) -> None:
    persona = await make_persona(db)
    lost = await make_persona(db, name="Оля")
    await request_check(db, lost.id, clock.now() - timedelta(minutes=30), clock.now())
    set_deps(deps)
    try:
        await agent.phone_check(persona.id)             # nothing due: a stale job
        await agent.attention_tick(0)
    finally:
        reset_deps()
    assert fakes.tasks.calls == [("phone_check", lost.id, 0)]


async def test_plan_day_queue_and_registration() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app)  # type: ignore[arg-type]
    await queue.plan_day(1, "2026-11-22")
    await queue.plan_day(1, "2026-11-22", True)       # one waiting plan per day
    assert app.deferred == [(PLAN_DAY_TASK, {"persona_id": 1, "day": "2026-11-22",
                                             "force": False},
                             {"queue": "agent", "queueing_lock": "life:1:2026-11-22"})]
    real = make_app(Settings(env="test"))
    assert {PLAN_DAY_TASK, LIFE_TICK_TASK} <= set(real.tasks)
    assert any(LIFE_TICK_TASK in str(key) for key in real.periodic_registry.periodic_tasks)


async def test_life_tasks_run_the_planner(db: AsyncSession, deps: Deps, fakes: Fakes) -> None:
    persona = await make_persona(db, models={"life": "life-model"}, timezone="Europe/Berlin")
    set_deps(deps)
    try:
        await agent.life_tick(0)
        await agent.plan_day(999_999, "2026-11-21")    # unknown persona: nothing to do
    finally:
        reset_deps()
    assert fakes.tasks.of("plan_day") == [("plan_day", persona.id, "2026-11-21", False),
                                          ("plan_day", persona.id, "2026-11-22", False)]


async def test_night_queue_and_registration() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app)  # type: ignore[arg-type]
    await queue.night_memory(1, "2026-11-20")
    await queue.night_memory(1, "2026-11-20")         # one night job per persona and day
    await queue.summarize_day(7, "2026-11-20", True)
    assert app.deferred == [
        (NIGHT_TASK, {"persona_id": 1, "day": "2026-11-20"},
         {"queue": "agent", "lock": "night:1", "queueing_lock": "night:1:2026-11-20"}),
        (SUMMARIZE_DAY_TASK, {"chat_id": 7, "day": "2026-11-20", "force": True},
         {"queue": "agent", "lock": "diary:7", "queueing_lock": "diary:7:2026-11-20"}),
    ]
    real = make_app(Settings(env="test"))
    assert {NIGHT_TASK, SUMMARIZE_DAY_TASK, MEMORY_TICK_TASK} <= set(real.tasks)
    assert any(MEMORY_TICK_TASK in str(key) for key in real.periodic_registry.periodic_tasks)


async def test_night_tasks_run_the_night_job(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    chat = await make_chat(db)
    await add_message(db, chat, "я врач", at=clock.now() - timedelta(days=1))
    fakes.llm.always("memory-model", '{"summary": "Дима рассказал, что он врач.", '
                                     '"facts": ["врач"]}')
    fakes.llm.always("passport-model", '{"text": "Жизнь: врач", "occupation": "врач"}')
    set_deps(deps)
    try:
        await agent.memory_tick(0)
        [(_, persona_id, day)] = fakes.tasks.of("night_memory")
        await agent.night_memory(persona_id, day)
        await agent.summarize_day(chat.id, day, True)
        await agent.night_memory(999_999, day)          # unknown persona: nothing to do
    finally:
        reset_deps()
    assert (persona_id, day) == (chat.persona_id, "2026-11-20")
    assert len(fakes.llm.calls_for("memory-model")) == 2            # the night + the rebuild
    client = await db.get(Client, chat.client_id)
    assert client is not None
    await db.refresh(client)
    assert client.passport["occupation"] == "врач"
