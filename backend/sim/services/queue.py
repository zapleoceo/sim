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
        """Compress the old part of the timeline into the summary and tidy the client memory."""
        ...
