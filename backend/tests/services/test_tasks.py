from __future__ import annotations

from typing import Any

import pytest
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.config import Settings
from sim.services.deps import Deps, get_deps, reset_deps, set_deps
from sim.services.voices import GEMINI_VOICES, VOICE_IDS, is_voice
from sim.tasks import agent
from sim.tasks.app import conninfo, make_app
from sim.tasks.queue import (
    GENERATE_TASK,
    MEMORY_TASK,
    REPLY_TASK,
    SEND_TASK,
    UNDERSTAND_TASK,
    ProcrastinateQueue,
)
from tests.services.conftest import Fakes
from tests.services.factories import add_message, make_chat, ok_verdict, plan_json


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
    ]


def test_conninfo_strips_the_sqlalchemy_driver() -> None:
    assert conninfo("postgresql+psycopg://u:p@db:5432/sim") == "postgresql://u:p@db:5432/sim"
    assert conninfo("postgresql://u:p@db/sim") == "postgresql://u:p@db/sim"


def test_make_app_registers_agent_tasks_under_shared_names() -> None:
    app = make_app(Settings(env="test"))
    assert {REPLY_TASK, UNDERSTAND_TASK, GENERATE_TASK, MEMORY_TASK} <= set(app.tasks)
    assert app.tasks[REPLY_TASK].queue == "agent"


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
    assert [call[0] for call in fakes.tasks.calls] == ["send"]
