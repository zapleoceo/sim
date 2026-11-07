"""Outbound messages: queue, human-like delay, delivery through the channel port, manual mode."""
from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Mapping
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import Channel, Dialog, Message
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.connectors.spec import Connector
from sim.domain.enums import Author, ChannelKind, ClientStatus, Delivery, Direction
from sim.ports.llm import LLMResult
from sim.services import jobkinds
from sim.services.dialog_state import apply_message
from sim.services.guard import GuardOutcome
from sim.services.send_receipts import UNCERTAIN_SEND, SendReceipt, SendReceipts, SendUncertain

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


def guard_fields(guard: GuardOutcome | None) -> dict[str, object]:
    if guard is None:
        return {}
    return {
        "guard_verdict": guard.verdict,
        "guard_violations": list(guard.violations),
        "guard_model": guard.model,
        "original_text": guard.original_text,
    }


def apply_receipt(message: Message, channel: Channel, receipt: SendReceipt) -> None:
    message.delivery, message.error = Delivery.SENT, None
    if receipt.external_id:
        message.external_id = receipt.external_id
    channel.last_activity_at = max(channel.last_activity_at or receipt.sent_at, receipt.sent_at)


class OutboundService:
    def __init__(
        self,
        session: AsyncSession,
        registry: Mapping[ChannelKind, Connector],
        box: SecretBox,
        *,
        factory: async_sessionmaker[AsyncSession],
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
        self.receipts = SendReceipts(factory, clock)

    async def queue_ai(
        self,
        dialog: Dialog,
        text: str,
        result: LLMResult | None,
        reply_delay_s: list[float] | None,
        *,
        final: bool = False,
        guard: GuardOutcome | None = None,
    ) -> Message:
        """Queue an AI reply (`text` is the final, guard-approved text; `guard` is recorded). `final` = the last thing the AI says before stepping back (a
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
            **guard_fields(guard),
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

    async def store_blocked(
        self, dialog: Dialog, result: LLMResult | None, guard: GuardOutcome
    ) -> Message:
        """The guard blocked the draft: keep it for the operator, never send it. The message does
        not touch the dialog preview / unread counters and has no SEND job."""
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.AI,
            text=guard.text,
            delivery=Delivery.CANCELLED,
            error=("blocked by the guard: " + "; ".join(guard.violations))[:500],
            created_at=self.clock(),
            model=result.model if result else None,
            provider=result.provider if result else None,
            tokens_in=result.tokens_in if result else None,
            tokens_out=result.tokens_out if result else None,
            cost_usd=result.cost_usd if result else None,
            **guard_fields(guard),
        )
        await self.messages.add(message)
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
        fingerprint = hashlib.sha256(json.dumps({
            "dialog_id": dialog.id, "channel_id": channel.id, "kind": channel.kind.value,
            "thread": dialog.external_thread_id, "text": message.text,
            "session_sha256": hashlib.sha256((channel.session_enc or "").encode()).hexdigest(),
        }, sort_keys=True).encode()).hexdigest()
        previous = await self.receipts.previous(message.id, fingerprint)
        if previous is not None:
            apply_receipt(message, channel, previous)
            return
        connector = self.registry.get(channel.kind)
        if connector is None:
            raise SendFailed(f"no connector for {channel.kind}")
        try:
            session = self.box.decrypt_json(channel.session_enc)
            port = connector.build_port(channel.id, session)
        except Exception as exc:  # connector errors are heterogeneous; the job retries
            raise SendFailed(f"{type(exc).__name__}: {exc}"[:300]) from exc

        await self.receipts.claim(message.id, fingerprint)
        try:
            result = await port.send_text(dialog.external_thread_id, message.text)
            if not result.ok:
                # The common transport contract cannot prove that a failed request had no effect.
                raise SendUncertain(UNCERTAIN_SEND)
            receipt = await self.receipts.acknowledge(
                message.id, fingerprint, result.external_message_id,
            )
        except Exception:
            # Cancellation/crash also leaves the committed intent: recovery refuses a new send.
            # Never persist a transport exception containing credentials or message text.
            raise SendUncertain(UNCERTAIN_SEND) from None
        apply_receipt(message, channel, receipt)

    async def mark_failed(self, message_id: int, error: str) -> None:
        message = await self.messages.get(message_id)
        if message is not None and message.delivery is not Delivery.SENT:
            message.delivery = Delivery.FAILED
            message.error = error[:500]
            dialog = await self.dialogs.get(message.dialog_id)
            if dialog is not None:
                dialog.needs_operator = True
