"""Job handlers. Each runs in its own unit of work (session); `giveup` runs once, after the last
failed attempt, in a fresh one."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_DIALOG, NS_SIM, lock, try_lock
from sim.adapters.db.models import SimMessage
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.repositories.simulations import SimBranchRepo, SimMessageRepo
from sim.services import jobkinds
from sim.services.conversation import AI_PAUSED, ReplyOutcome
from sim.wiring import Container

# A handler may return a small dict about what it did; it lands in the job step of the trace log.
Run = Callable[[Container, AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class Handler:
    run: Run
    giveup: Run | None = None


async def run_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    dialog_id = int(payload["dialog_id"])
    outcome = await c.conversation(s).run_reply(dialog_id)
    if outcome.reason == AI_PAUSED:   # not a silent skip: the journal names the dialog and message
        await c.reply_journal(s).paused(dialog_id, outcome.inbound_id, source="reply_job")
    return reply_detail(outcome)


def reply_detail(outcome: ReplyOutcome) -> dict[str, Any]:
    """What the reply pipeline did (sent / blocked / superseded...) and the guard's verdict."""
    message = outcome.message
    detail = {"outcome": outcome.status, "message_id": message.id if message else None,
              "guard": message.guard_verdict if message else None}
    if outcome.reason:
        detail["reason"] = outcome.reason
    return detail


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


async def run_passport(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """The passportist agent rewrites the lead passport after a persona reply."""
    return {"updated": await c.passport(s).update(int(payload["dialog_id"]))}


async def run_video_note(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """An AI video note for the allowlisted owner account: cache or Broker, then label + note."""
    return await c.video_notes(s).run(payload)


async def run_persona_media(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """The photo or video note a persona chose to attach to her reply: generate, then send."""
    return await c.media_tool(s).run(payload)


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
    if not payload.get("web"):
        await c.admin_bot(s).run_reply_job(payload)
        return
    branch_id = int(payload["branch_id"])
    if not await try_lock(s, NS_SIM, branch_id):
        raise RuntimeError("Simulation reply is already running")
    branch = await SimBranchRepo(s).get(branch_id)
    message = await SimMessageRepo(s).get(int(payload["message_id"]))
    if branch is None or message is None:
        return
    last = (await SimMessageRepo(s).recent(branch_id, 1))[-1:]
    if not last or last[0].id != message.id or message.role != "client":
        return
    result = await c.simulation(s).reply(branch, str(payload["client_name"]))
    if result.status in ("no_decision", "skipped"):
        await SimMessageRepo(s).add(SimMessage(
            branch_id=branch_id, role="system",
            text=result.note or "Модель не смогла дать проверенный ответ.",
            created_at=c.clock(),
        ))


async def giveup_sim_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    if not payload.get("web"):
        await c.admin_bot(s).give_up(payload)
        return
    branch_id = int(payload["branch_id"])
    rows = await SimMessageRepo(s).recent(branch_id, 1)
    if rows and rows[-1].role == "client":
        await SimMessageRepo(s).add(SimMessage(
            branch_id=branch_id, role="system",
            text="Не удалось получить ответ модели. Повторите сообщение позже.",
            created_at=c.clock(),
        ))


HANDLERS: dict[str, Handler] = {
    jobkinds.REPLY: Handler(run_reply, giveup_reply),
    jobkinds.MEDIA: Handler(run_media, giveup_media),
    jobkinds.FOLLOWUP: Handler(run_followup, giveup_followup),
    jobkinds.SEND: Handler(run_send, giveup_send),
    jobkinds.PASSPORT: Handler(run_passport),
    jobkinds.VIDEO_NOTE: Handler(run_video_note),
    jobkinds.PERSONA_MEDIA: Handler(run_persona_media),
    jobkinds.CONTENT_PUBLISH: Handler(run_content_publish),
    jobkinds.SIM_REPLY: Handler(run_sim_reply, giveup_sim_reply),
}
