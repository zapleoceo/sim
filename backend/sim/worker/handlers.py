"""Job handlers. Each runs in its own unit of work (session); `giveup` runs once, after the last
failed attempt, in a fresh one."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_DIALOG, lock
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.services import jobkinds
from sim.services.conversation import ReplyOutcome
from sim.wiring import Container

# A handler may return a small dict about what it did; it lands in the job step of the trace log.
Run = Callable[[Container, AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class Handler:
    run: Run
    giveup: Run | None = None


async def run_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return reply_detail(await c.conversation(s).run_reply(int(payload["dialog_id"])))


def reply_detail(outcome: ReplyOutcome) -> dict[str, Any]:
    """What the reply pipeline did (sent / blocked / superseded...) and the guard's verdict."""
    message = outcome.message
    return {"outcome": outcome.status, "message_id": message.id if message else None,
            "guard": message.guard_verdict if message else None}


async def giveup_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    """The model could not answer after all retries: hand the dialog to a human."""
    dialog_id = int(payload["dialog_id"])
    await lock(s, NS_DIALOG, dialog_id)
    dialog = await DialogRepo(s).get(dialog_id)
    if dialog is not None:
        dialog.needs_operator = True
        dialog.ai_paused = True


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


async def run_followup(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": await c.followup(s).run(
        int(payload["dialog_id"]),
        int(payload["step"]),
        int(payload["after_message_id"]),
        c.conversation(s),
    )}


async def run_send(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.outbound(s).deliver(int(payload["message_id"]), final=bool(payload.get("final")))


async def giveup_send(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.outbound(s).mark_failed(
        int(payload["message_id"]),
        "Delivery failed or uncertain; reconcile the channel before resending",
    )


async def run_content_publish(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.content(s).publish(int(payload["post_id"]))


async def run_sim_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.admin_bot(s).run_reply_job(payload)


async def giveup_sim_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.admin_bot(s).give_up(payload)


HANDLERS: dict[str, Handler] = {
    jobkinds.REPLY: Handler(run_reply, giveup_reply),
    jobkinds.MEDIA: Handler(run_media, giveup_media),
    jobkinds.FOLLOWUP: Handler(run_followup, giveup_followup),
    jobkinds.SEND: Handler(run_send, giveup_send),
    jobkinds.CONTENT_PUBLISH: Handler(run_content_publish),
    jobkinds.SIM_REPLY: Handler(run_sim_reply, giveup_sim_reply),
}
