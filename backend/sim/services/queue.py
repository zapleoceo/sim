"""The background work a service may ask for. Services call this interface after their
transaction commits; the Procrastinate implementation lives in sim/tasks/queue.py and tests use
a recording fake."""
from __future__ import annotations

from typing import Protocol


class TaskQueue(Protocol):
    async def reply(self, chat_id: int, delay_s: float = 0) -> None:
        """Answer the chat. One chat is processed strictly in order; a request made while one is
        already waiting is absorbed by it (the reply task re-checks the chat when it starts)."""
        ...

    async def phone_check(self, persona_id: int, delay_s: float = 0) -> None:
        """She looks at her phone (docs/attention.md). Every request is a job; only the one
        whose time matches `persona.attention_check_at` does the check, the others find
        nothing due and finish."""
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
        """Compress the old part of the timeline into the chat summary."""
        ...

    async def update_passport(self, chat_id: int, delay_s: float = 0) -> None:
        """Rewrite the client's passport from this chat (docs/leads.md). A request made while
        one is waiting is absorbed by it; one chat's updates never run in parallel."""
        ...

    async def plan_day(self, persona_id: int, day: str, force: bool = False) -> None:
        """Plan one day (ISO date, her local calendar) of the persona's life. Without `force`
        an existing plan is kept; `force` re-plans (the owner's «Спланировать заново»)."""
        ...

    async def mark_read(self, message_id: int) -> None:
        """Mark the chat read in Telegram up to this incoming message (best effort)."""
        ...

    async def chat_action(self, chat_id: int, action: str, seconds: float) -> None:
        """Show a chat action (`sim.domain.presence.ChatAction`) for up to `seconds` (bounded
        to 300 s), re-sent while it lasts; `cancel` stops it. Requests of one chat apply in
        order; a new one replaces the previous."""
        ...
