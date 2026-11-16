"""Job handlers. Each runs in its own unit of work (session); `giveup` runs once, after the last
failed attempt, in a fresh one."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_DIALOG, NS_SIM, lock, try_lock
from sim.adapters.db.models import Message, SimMessage
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.repositories.simulations import SimBranchRepo, SimMessageRepo
from sim.services import jobkinds, media_decisions, tracing
from sim.services.conversation import AI_PAUSED, ReplyOutcome
from sim.services.journal import HANDOFF, CommunicationJournal
from sim.services.reengagement import ReengagementService
from sim.wiring import Container

# A handler may return a small dict about what it did; it lands in the job step of the trace log.
Run = Callable[[Container, AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class Handler:
    run: Run
    giveup: Run | None = None


async def run_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    dialog_id = int(payload["dialog_id"])
    outcome = await c.conversation(s).run_reply(dialog_id, delay=payload.get("delay"))
    if outcome.reason == AI_PAUSED:   # not a silent skip: the journal names the dialog and message
        await c.reply_journal(s).paused(dialog_id, outcome.inbound_id, source="reply_job")
    await journal_media(c, s, dialog_id, outcome)
    return reply_detail(outcome)


async def journal_media(c: Container, s: AsyncSession, dialog_id: int, outcome: ReplyOutcome) -> None:
    """Her media decision as a step of the system log of its own: a warning when the owner asked
    and she refused, or when her reason is not confirmed (sim.services.media_decisions)."""
    media = outcome.media
    if media is None:
        return
    look = media.needs_a_look
    await c.reply_journal(s).write(
        f"{media_decisions.TAG.lower()}: {media.headline}",
        tracing.WARNING if look else tracing.OK, logging.WARNING if look else logging.INFO,
        dialog_id, outcome.message.id if outcome.message else None, **media.journal())


def reply_detail(outcome: ReplyOutcome) -> dict[str, Any]:
    """What the reply pipeline did (sent / blocked / superseded...), the guard's verdict and her
    media decision when there was one (sim.services.media_decisions)."""
    message = outcome.message
    detail = {"outcome": outcome.status, "message_id": message.id if message else None,
              "guard": message.guard_verdict if message else None}
    if outcome.reason:
        detail["reason"] = outcome.reason
    if outcome.media is not None:
        detail["media"] = outcome.media.journal()
    return detail


async def giveup_reply(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    """The model could not answer after all retries: hand the dialog to a human."""
    dialog_id = int(payload["dialog_id"])
    await lock(s, NS_DIALOG, dialog_id)
    dialog = await DialogRepo(s).get(dialog_id)
    if dialog is not None:
        dialog.needs_operator = True
        dialog.ai_paused = True
        await CommunicationJournal(s, clock=c.clock).record(
            dialog_id, HANDOFF, new="operator",
            reason="модель не смогла ответить после всех попыток")


async def giveup_followup(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    """The follow-up could not be produced / guarded after all retries: a human decides."""
    dialog = await DialogRepo(s).get(int(payload["dialog_id"]))
    if dialog is not None:
        dialog.needs_operator = True
        await CommunicationJournal(s, clock=c.clock).record(
            dialog.id, HANDOFF, new="operator",
            reason="напоминание клиенту не удалось создать после всех попыток")


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
    # Retire pre-rollout ladder jobs. The silence sweep owns the sole new plan.
    return {"outcome": "replaced_by_contextual_plan"}


async def run_reengagement(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": await ReengagementService(s, clock=c.clock).analyze(
        int(payload["dialog_id"]), int(payload["revision"]), c.conversation(s))}


async def giveup_reengagement(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await ReengagementService(s, clock=c.clock).failed(
        int(payload["dialog_id"]), int(payload["revision"]))


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
    if "reengagement_revision" in payload:
        await send_reengagement(c, s, payload)
        return
    await c.outbound(s).deliver(
        int(payload["message_id"]), final=bool(payload.get("final")),
        typing_s=float(payload.get("typing_s") or 0.0),
    )


async def send_reengagement(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    """Fresh time review, then the serialized transport boundary. No rewrite sent invisibly."""
    from sim.adapters.db.models import OutboundSendIntent
    from sim.domain.enums import Delivery
    from sim.ports.llm import LLMResult
    from sim.services.conversation import history_turns, with_persona_facts
    from sim.services.dialog_state import apply_message
    from sim.services.prompt import Decision

    message = await s.get(Message, int(payload["message_id"]))
    if message is None or message.delivery in (Delivery.SENT, Delivery.CANCELLED, Delivery.FAILED):
        return
    conv = c.conversation(s)
    dialog = await conv.dialogs.get(message.dialog_id)
    service = ReengagementService(s, clock=c.clock)
    revision = int(payload["reengagement_revision"])
    if dialog is None:
        return
    if not await service.send_valid(dialog, message, revision, cancel=False):
        await lock(s, NS_DIALOG, dialog.id)
        dialog = await conv.reload(dialog.id)
        await s.refresh(message)
        await service.send_valid(dialog, message, revision)
        return
    persona = await conv.personas.get(dialog.client.persona_id)
    recent, ctx = await conv.memory(dialog.id, await conv.dialog_context(dialog, persona),
                                     refresh_summary=False)
    ctx = with_persona_facts(persona, ctx)
    guard = await conv.review(await conv.strategy_for(persona.id), history_turns(recent),
        Decision(message.text), LLMResult(text=message.text, model=message.model), ctx, dialog.id)
    await lock(s, NS_DIALOG, dialog.id)
    dialog = await conv.reload(dialog.id)
    await s.refresh(message)
    await s.refresh(persona)
    if message.delivery is not Delivery.QUEUED:
        return
    if not await service.send_valid(dialog, message, revision):
        return
    if not guard.allowed or guard.text != message.text:
        message.delivery = Delivery.CANCELLED
        dialog.reengagement_plan = {**dialog.reengagement_plan, "status": "outdated",
                                    "reason": "Перед отправкой требуется обновить текст"}
        dialog.activity_revision += 1
        return
    await c.outbound(s).deliver(message.id)
    plan = dict(dialog.reengagement_plan)
    if message.delivery is Delivery.SENT:
        receipt = await s.get(OutboundSendIntent, message.id)
        message.created_at = receipt.sent_at
        apply_message(dialog, message)
        # Keep the analyzed revision: delivery is new context requiring another assessment.
        plan.update(last_sent_id=message.id, delivered_at=receipt.sent_at.isoformat() + "Z")
    dialog.reengagement_plan = {**plan, "status": message.delivery.value}


async def giveup_send(c: Container, s: AsyncSession, payload: dict[str, Any]) -> None:
    await c.outbound(s).mark_failed(
        int(payload["message_id"]),
        "Delivery failed or uncertain; reconcile the channel before resending",
    )
    if "reengagement_revision" in payload:
        message = await s.get(Message, int(payload["message_id"]))
        if message is not None:
            await lock(s, NS_DIALOG, message.dialog_id)
            dialog = await DialogRepo(s).get(message.dialog_id)
            if dialog and (dialog.reengagement_plan or {}).get("message_id") == message.id:
                dialog.reengagement_plan = {**dialog.reengagement_plan, "status": "failed",
                    "reason": "Доставка не подтверждена; требуется сверка канала, повтор запрещён"}


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
    jobkinds.REENGAGEMENT: Handler(run_reengagement, giveup_reengagement),
    jobkinds.SEND: Handler(run_send, giveup_send),
    jobkinds.PASSPORT: Handler(run_passport),
    jobkinds.VIDEO_NOTE: Handler(run_video_note),
    jobkinds.PERSONA_MEDIA: Handler(run_persona_media),
    jobkinds.CONTENT_PUBLISH: Handler(run_content_publish),
    jobkinds.SIM_REPLY: Handler(run_sim_reply, giveup_sim_reply),
}
