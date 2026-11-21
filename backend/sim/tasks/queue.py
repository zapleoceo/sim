"""TaskQueue on Procrastinate. Deferring is by task name, so a process can queue tasks it does
not run itself (e.g. the worker queues `telegram.send_message`).

Ordering and de-duplication:
- `lock = chat:<id>` on reply and memory jobs: one chat is processed strictly in order;
- `queueing_lock` per chat for replies: while a reply is waiting to start, a new request is
  absorbed by it (the reply task re-checks the chat and waits for a burst when it starts);
- `lock = send:<chat id>` keeps one chat's outgoing messages in order;
- `queueing_lock = life:<persona>:<day>`: one waiting plan per day of the persona's life;
- `lock` and `queueing_lock = passport:<chat id>`: a burst of turns makes one passport update,
  and one chat's updates never overlap;
- `lock = chat-action:<chat id>` applies one chat's status requests in order;
- phone checks have no lock: the database decides which job is the persona's pending check
  (docs/attention.md)."""
from __future__ import annotations

import logging
from typing import Any

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued

log = logging.getLogger(__name__)

AGENT_QUEUE = "agent"
TELEGRAM_QUEUE = "telegram"

REPLY_TASK = "agent.reply_chat"
UNDERSTAND_TASK = "agent.understand_media"
GENERATE_TASK = "agent.run_generation"
MEMORY_TASK = "agent.refresh_memory"
PASSPORT_TASK = "agent.update_passport"
PLAN_DAY_TASK = "agent.plan_day"
LIFE_TICK_TASK = "agent.life_tick"     # periodic: which days need a plan
LIFE_TICK_CRON = "*/30 * * * *"
PHONE_CHECK_TASK = "agent.phone_check"
ATTENTION_TICK_TASK = "agent.attention_tick"    # periodic: phone checks whose job was lost
ATTENTION_TICK_CRON = "*/5 * * * *"
SEND_TASK = "telegram:send_message"   # sim.telegram.tasks blueprint, namespace "telegram"
MARK_READ_TASK = "telegram:mark_read"
CHAT_ACTION_TASK = "telegram:chat_action"


def chat_lock(chat_id: int) -> str:
    return f"chat:{chat_id}"


class ProcrastinateQueue:
    def __init__(self, app: procrastinate.App) -> None:
        self.app = app

    async def _defer(self, task: str, *, queue: str, kwargs: dict[str, Any],
                     lock: str | None = None, queueing_lock: str | None = None,
                     delay_s: float = 0) -> None:
        options: dict[str, Any] = {"queue": queue}
        if lock:
            options["lock"] = lock
        if queueing_lock:
            options["queueing_lock"] = queueing_lock
        if delay_s > 0:
            options["schedule_in"] = {"seconds": delay_s}
        try:
            await self.app.configure_task(task, **options).defer_async(**kwargs)
        except AlreadyEnqueued:
            log.debug("%s %s already waiting (%s)", task, kwargs, queueing_lock)

    async def reply(self, chat_id: int, delay_s: float = 0) -> None:
        await self._defer(REPLY_TASK, queue=AGENT_QUEUE, kwargs={"chat_id": chat_id},
                          lock=chat_lock(chat_id), queueing_lock=f"reply:{chat_id}",
                          delay_s=delay_s)

    async def phone_check(self, persona_id: int, delay_s: float = 0) -> None:
        # Neither lock nor queueing lock: an earlier check must never wait behind (or be
        # absorbed by) a later one; `persona.attention_check_at` makes stale jobs no-ops.
        await self._defer(PHONE_CHECK_TASK, queue=AGENT_QUEUE,
                          kwargs={"persona_id": persona_id}, delay_s=delay_s)

    async def understand(self, media_id: int) -> None:
        await self._defer(UNDERSTAND_TASK, queue=AGENT_QUEUE, kwargs={"media_id": media_id},
                          queueing_lock=f"understand:{media_id}")

    async def generate(self, generation_id: int) -> None:
        await self._defer(GENERATE_TASK, queue=AGENT_QUEUE,
                          kwargs={"generation_id": generation_id},
                          queueing_lock=f"generation:{generation_id}")

    async def send(self, message_id: int, chat_id: int | None = None) -> None:
        await self._defer(SEND_TASK, queue=TELEGRAM_QUEUE, kwargs={"message_id": message_id},
                          lock=f"send:{chat_id}" if chat_id is not None else None,
                          queueing_lock=f"send-message:{message_id}")

    async def refresh_memory(self, chat_id: int) -> None:
        await self._defer(MEMORY_TASK, queue=AGENT_QUEUE, kwargs={"chat_id": chat_id},
                          lock=chat_lock(chat_id), queueing_lock=f"memory:{chat_id}")

    async def update_passport(self, chat_id: int, delay_s: float = 0) -> None:
        await self._defer(PASSPORT_TASK, queue=AGENT_QUEUE, kwargs={"chat_id": chat_id},
                          lock=f"passport:{chat_id}", queueing_lock=f"passport:{chat_id}",
                          delay_s=delay_s)

    async def plan_day(self, persona_id: int, day: str, force: bool = False) -> None:
        await self._defer(PLAN_DAY_TASK, queue=AGENT_QUEUE,
                          kwargs={"persona_id": persona_id, "day": day, "force": force},
                          queueing_lock=f"life:{persona_id}:{day}")

    async def mark_read(self, message_id: int) -> None:
        await self._defer(MARK_READ_TASK, queue=TELEGRAM_QUEUE, kwargs={"message_id": message_id},
                          queueing_lock=f"mark-read:{message_id}")

    async def chat_action(self, chat_id: int, action: str, seconds: float) -> None:
        # No queueing lock: a `cancel` must never be absorbed by a waiting `typing`.
        await self._defer(CHAT_ACTION_TASK, queue=TELEGRAM_QUEUE,
                          kwargs={"chat_id": chat_id, "action": action,
                                  "seconds": float(seconds)},
                          lock=f"chat-action:{chat_id}")
