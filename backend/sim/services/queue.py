"""The background work a service may ask for. Services call this interface after their
transaction commits; the Procrastinate implementation lives in sim/tasks/queue.py and tests use
a recording fake."""
from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Protocol

log = logging.getLogger(__name__)


async def after_commit(what: str, defer: Awaitable[None]) -> None:
    """Defers the job of a row that is already committed. A failure is logged, never raised:
    the row is stored and its outcome is final (a sent turn stays sent); the reaper
    (sim/services/reaper.py) queues a pending message or a queued generation again."""
    try:
        await defer
    except Exception:
        log.exception("%s not queued; the reaper will queue it", what)


class TaskQueue(Protocol):
    async def reply(self, chat_id: int, delay_s: float = 0) -> None:
        """Answer the chat. One chat is processed strictly in order; a request made while one is
        already waiting is absorbed by it (the reply task re-checks the chat when it starts)."""
        ...

    async def phone_check(self, persona_id: int, delay_s: float = 0) -> None:
        """She looks at her phone in `delay_s` (docs/attention.md). Every request is a job;
        only the one whose time matches `persona.attention_check_at` does the check, the
        others find nothing due and finish."""
        ...

    async def look_at_phone(self, persona_id: int) -> None:
        """The check itself, now (asked for by the `phone_check` job when its time comes). Two
        checks of one persona never run at the same time: one asked for while another runs
        waits and runs right after it."""
        ...

    async def understand(self, media_id: int) -> None:
        """Describe / transcribe an incoming attachment."""
        ...

    async def generate(self, generation_id: int) -> None:
        """Make a photo / video the persona asked for and send it."""
        ...

    async def send(self, message_id: int, chat_id: int | None = None) -> None:
        """Hand a pending outgoing message to the messenger process. `chat_id` keeps one chat's
        messages in order; without it the message is sent on its own."""
        ...

    async def refresh_memory(self, chat_id: int) -> None:
        """Compress the oldest part of today into the «утром…» paragraph when the day no
        longer fits the context budget (docs/memory.md)."""
        ...

    async def update_passport(self, chat_id: int, delay_s: float = 0) -> None:
        """Rewrite the client's passport from this chat's today (docs/leads.md; the owner's
        «Обновить паспорт сейчас» — at night the night job does it). A request made while one
        is waiting is absorbed by it; one chat's updates never run in parallel."""
        ...

    async def night_memory(self, persona_id: int, day: str) -> None:
        """Summarize one finished day (ISO date, her calendar) of every chat of the persona,
        update the passports, roll a finished week up (docs/memory.md). One job per persona
        and day; one persona's nights never run in parallel."""
        ...

    async def summarize_day(self, chat_id: int, day: str, force: bool = False) -> None:
        """(Re)write one chat's day summary and then its passport; `force` overwrites an
        edited day (the owner's «Пересобрать день»)."""
        ...

    async def plan_day(self, persona_id: int, day: str, force: bool = False) -> None:
        """Plan one day (ISO date, her local calendar) of the persona's life. Without `force`
        an existing plan is kept; `force` re-plans (the owner's «Спланировать заново»)."""
        ...

    async def mark_read(self, message_id: int) -> None:
        """Mark the chat read in Telegram up to this incoming message (best effort)."""
        ...

    async def poll_instagram(self, channel_id: int, delay_s: float = 0) -> None:
        """Read an Instagram account's Direct once (docs/channels.md, «Приём»)."""
        ...

    async def whatsapp_catch_up(self, channel_id: int) -> None:
        """Read what a WhatsApp account got while it was offline (after a reconnect)."""
        ...

    async def sync_stickers(self, channel_id: int) -> None:
        """Re-read the stickers of a Telegram account (docs/stickers.md)."""
        ...

    async def place_call(self, contact_id: int) -> None:
        """Her call of a due planned contact, placed by the telegram process
        (docs/initiative.md). One job per contact."""
        ...

    async def chat_action(self, chat_id: int, action: str, seconds: float) -> None:
        """Show a chat action (`sim.domain.presence.ChatAction`) for up to `seconds` (bounded
        to 300 s), re-sent while it lasts; `cancel` stops it. Requests of one chat apply in
        order; a new one replaces the previous."""
        ...
