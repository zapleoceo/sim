"""Follow-ups for a silent client, by strategy.followup_hours (cumulative hours since the AI's
last message, e.g. [24, 72, 168]). After the last follow-up the client becomes no_response."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Dialog, Message, Strategy
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus, Direction
from sim.domain.funnel import Actor
from sim.services import jobkinds
from sim.services.funnel import FunnelService

if TYPE_CHECKING:
    from sim.services.conversation import ConversationService

MIN_GAP_H = 0.01


def step_delay_h(hours: list[float], step: int) -> float:
    """Hours to wait before follow-up `step` counted from the previous rung."""
    if step == 0:
        return float(hours[0])
    return max(float(hours[step]) - float(hours[step - 1]), MIN_GAP_H)


def followup_instruction(hours: list[float], step: int) -> str:
    last = step + 1 >= len(hours)
    text = (
        f"ЗАДАЧА НАПОМИНАНИЯ: клиент не отвечает примерно {hours[step]:g} ч. "
        f"Это напоминание {step + 1} из {len(hours)}. Напиши ОДНО короткое, дружелюбное, "
        "ненавязчивое сообщение, которое снова открывает разговор и цепляется за что-то из "
        "диалога. Не повторяй своё прошлое сообщение и не дави."
    )
    if last:
        text += " Это последнее напоминание: вежливо заверши и оставь дверь открытой."
    return text


class FollowupService:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.session = session
        self.clock = clock
        self.jobs = JobRepo(session)
        self.messages = MessageRepo(session)
        self.dialogs = DialogRepo(session)

    async def cancel(self, dialog_id: int) -> None:
        await self.jobs.cancel_pending(jobkinds.FOLLOWUP, jobkinds.followup_key(dialog_id))

    async def schedule(self, dialog: Dialog, strategy: Strategy, step: int, after: Message) -> bool:
        hours = [float(h) for h in (strategy.followup_hours or [])]
        if step >= len(hours) or dialog.client.no_followups:
            return False
        await self.cancel(dialog.id)
        await self.jobs.enqueue(
            jobkinds.FOLLOWUP,
            {"dialog_id": dialog.id, "step": step, "after_message_id": after.id},
            self.clock() + timedelta(hours=step_delay_h(hours, step)),
            dedup_key=jobkinds.followup_key(dialog.id),
        )
        return True

    async def run(
        self, dialog_id: int, step: int, after_message_id: int, conversation: ConversationService
    ) -> str:
        """Execute one rung. Returns "sent" or the reason it was skipped."""
        dialog = await self.dialogs.get(dialog_id)
        if dialog is None:
            return "no_dialog"
        client = dialog.client
        if dialog.ai_paused or client.no_followups or client.status is not ClientStatus.ACTIVE:
            return "not_active"
        last_in = await self.messages.last(dialog_id, Direction.IN)
        if last_in is not None and last_in.id > after_message_id:
            return "client_replied"
        strategy = await conversation.strategy_for(client.persona_id)
        hours = [float(h) for h in (strategy.followup_hours or [])]
        if step >= len(hours):
            return "no_such_step"
        outcome = await conversation.run_reply(
            dialog_id, instruction=followup_instruction(hours, step), workflow="followup"
        )
        if outcome.message is None:
            return outcome.status
        dialog.followup_step = step + 1
        if step + 1 < len(hours):
            await self.schedule(dialog, strategy, step + 1, outcome.message)
        else:
            await FunnelService(self.session).set_status(
                client, ClientStatus.NO_RESPONSE, Actor.SYSTEM, "follow-up ladder exhausted"
            )
        return "sent"
