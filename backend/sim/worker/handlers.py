"""Job handlers. Each runs in its own unit of work (session); `giveup` runs once, after the last
failed attempt, in a fresh one."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.repositories.conversations import DialogRepo
from sim.services import jobkinds
from sim.wiring import Container

Run = Callable[[Container, AsyncSession, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class Handler:
    run: Run
    giveup: Run | None = None


async def run_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.conversation(s).run_reply(int(payload["dialog_id"]))


async def giveup_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    """The model could not answer after all retries: hand the dialog to a human."""
    dialog = await DialogRepo(s).get(int(payload["dialog_id"]))
    if dialog is not None:
        dialog.needs_operator = True


async def giveup_followup(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    """The follow-up could not be produced / guarded after all retries: a human decides."""
    dialog = await DialogRepo(s).get(int(payload["dialog_id"]))
    if dialog is not None:
        dialog.needs_operator = True


async def run_media(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.media_service(s).process(
        int(payload["message_id"]), str(payload["ref"]), str(payload["kind"]),
        transcribe=bool(payload.get("transcribe")),
    )


async def giveup_media(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.media_service(s).give_up(
        int(payload["message_id"]), str(payload["kind"]), transcribe=bool(payload.get("transcribe"))
    )


async def run_followup(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.followup(s).run(
        int(payload["dialog_id"]),
        int(payload["step"]),
        int(payload["after_message_id"]),
        c.conversation(s),
    )


async def run_send(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.outbound(s).deliver(int(payload["message_id"]), final=bool(payload.get("final")))


async def giveup_send(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.outbound(s).mark_failed(int(payload["message_id"]), "delivery failed after retries")


async def run_content_publish(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.content(s).publish(int(payload["post_id"]))


HANDLERS: dict[str, Handler] = {
    jobkinds.REPLY: Handler(run_reply, giveup_reply),
    jobkinds.MEDIA: Handler(run_media, giveup_media),
    jobkinds.FOLLOWUP: Handler(run_followup, giveup_followup),
    jobkinds.SEND: Handler(run_send, giveup_send),
    jobkinds.CONTENT_PUBLISH: Handler(run_content_publish),
}
