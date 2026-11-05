"""Outbound messages: queue, human-like delay, delivery through the channel port, manual mode."""
from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import Dialog, Message
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.connectors.spec import Connector
from sim.domain.enums import Author, ChannelKind, ClientStatus, Delivery, Direction
from sim.ports.llm import LLMResult
from sim.services import jobkinds
from sim.services.dialog_state import apply_message

DEFAULT_DELAY_S = (20.0, 90.0)


class SendFailed(Exception):
    """Delivery did not succeed; the worker retries with backoff."""


def pick_delay(reply_delay_s: list[float] | None, rng: Callable[[float, float], float]) -> float:
    lo, hi = DEFAULT_DELAY_S
    if reply_delay_s and len(reply_delay_s) == 2:
        lo, hi = float(reply_delay_s[0]), float(reply_delay_s[1])
    lo, hi = max(lo, 0.0), max(hi, 0.0)
    return rng(min(lo, hi), max(lo, hi))


def send_key(message_id: int) -> str:
    return f"send:{message_id}"


async def cancel_queued_ai(session: AsyncSession, dialog_id: int, reason: str) -> int:
    """A human took over (or the AI was paused): AI replies still waiting for their human-like
    delay must not go out. Marks them cancelled and cancels their SEND jobs."""
    rows = (
        await session.scalars(
            select(Message).where(
                Message.dialog_id == dialog_id,
                Message.author == Author.AI,
                Message.delivery == Delivery.QUEUED,
            )
        )
    ).all()
    jobs = JobRepo(session)
    for message in rows:
        message.delivery = Delivery.CANCELLED
        message.error = reason[:500]
        await jobs.cancel_pending(jobkinds.SEND, send_key(message.id))
    return len(rows)


class OutboundService:
    def __init__(
        self,
        session: AsyncSession,
        registry: Mapping[ChannelKind, Connector],
        box: SecretBox,
        *,
        clock: Clock = utcnow,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.session = session
        self.registry = registry
        self.box = box
        self.clock = clock
        self.rng = rng
        self.messages = MessageRepo(session)
        self.dialogs = DialogRepo(session)
        self.jobs = JobRepo(session)

    async def queue_ai(
        self,
        dialog: Dialog,
        text: str,
        result: LLMResult | None,
        reply_delay_s: list[float] | None,
        *,
        final: bool = False,
    ) -> Message:
        """Queue an AI reply. `final` = the last thing the AI says before stepping back (a
        refusal farewell, a hand-off note): it is still delivered although the dialog is
        paused / the client refused by the time it is sent."""
        now = self.clock()
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.AI,
            text=text,
            delivery=Delivery.QUEUED,
            created_at=now,
            model=result.model if result else None,
            provider=result.provider if result else None,
            tokens_in=result.tokens_in if result else None,
            tokens_out=result.tokens_out if result else None,
            cost_usd=result.cost_usd if result else None,
        )
        await self.messages.add(message)
        apply_message(dialog, message)
        delay = pick_delay(reply_delay_s, self.rng)
        await self.jobs.enqueue(
            jobkinds.SEND,
            {"message_id": message.id, "final": final},
            now + timedelta(seconds=delay),
            dedup_key=send_key(message.id),
        )
        return message

    async def send_manual(self, dialog: Dialog, operator_id: int, text: str) -> Message:
        """An operator writes in the dialog: the AI steps back on this dialog."""
        now = self.clock()
        await cancel_queued_ai(self.session, dialog.id, "cancelled: an operator took over")
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.OPERATOR,
            text=text,
            delivery=Delivery.QUEUED,
            created_at=now,
        )
        await self.messages.add(message)
        apply_message(dialog, message)
        dialog.ai_paused = True
        dialog.needs_operator = False
        dialog.operator_id = operator_id
        await self.jobs.cancel_pending(jobkinds.REPLY, jobkinds.reply_key(dialog.id))
        await self.jobs.cancel_pending(jobkinds.FOLLOWUP, jobkinds.followup_key(dialog.id))
        await self.jobs.enqueue(
            jobkinds.SEND, {"message_id": message.id}, now, dedup_key=send_key(message.id)
        )
        return message

    async def deliver(self, message_id: int, *, final: bool = False) -> None:
        message = await self.messages.get(message_id)
        if message is None or message.delivery is Delivery.SENT:
            return
        dialog = await self.dialogs.get(message.dialog_id)
        if dialog is None:
            raise SendFailed("dialog not found")
        if message.delivery is Delivery.CANCELLED:
            return
        if (
            message.author is Author.AI
            and not final
            and (dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED)
        ):
            # Queued while the AI was active; a human took over / the client refused since.
            message.delivery = Delivery.CANCELLED
            message.error = "cancelled: AI paused or client refused before sending"
            return
        channel = dialog.channel
        connector = self.registry.get(channel.kind)
        if connector is None:
            raise SendFailed(f"no connector for {channel.kind}")
        try:
            session = self.box.decrypt_json(channel.session_enc)
            port = connector.build_port(channel.id, session)
            result = await port.send_text(dialog.external_thread_id, message.text)
        except Exception as exc:  # connector errors are heterogeneous; the job retries
            raise SendFailed(f"{type(exc).__name__}: {exc}"[:300]) from exc
        if not result.ok:
            raise SendFailed(result.error or "send failed")
        message.delivery = Delivery.SENT
        message.error = None
        if result.external_message_id:
            message.external_id = result.external_message_id
        channel.last_activity_at = self.clock()

    async def mark_failed(self, message_id: int, error: str) -> None:
        message = await self.messages.get(message_id)
        if message is not None and message.delivery is not Delivery.SENT:
            message.delivery = Delivery.FAILED
            message.error = error[:500]
