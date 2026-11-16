"""One versioned silence plan per dialog. Five minutes starts analysis, never forces a send.

The worker sweep does DB work only. Model work runs in a fenced job; sending uses the ordinary
durable send intent. NS_DIALOG serializes its final check/transport with incoming messages.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_DIALOG, NS_REPLY, lock, try_lock
from sim.adapters.db.models import Dialog, Message, Persona
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, to_naive_utc, utcnow
from sim.domain.enums import Author, ClientStatus, Delivery, Direction, SessionStatus
from sim.services import jobkinds
from sim.services.guard import GuardError
from sim.services.persona_clock import persona_facts
from sim.services.prompt import extract_json_object, one_line
from sim.services.reply_schedule import Incoming, answers_at_once, plan_reply, rules_from

if TYPE_CHECKING:
    from sim.services.conversation import ConversationService

SILENCE = timedelta(minutes=5)
MAX_DELAY = timedelta(days=7)
SEND_GRACE = timedelta(minutes=5)

INSTRUCTION = """Прошло не меньше пяти минут тишины. Это порог анализа, НЕ срок отправки.
Проанализируй весь предоставленный диалог, паспорт, данные лида, распорядок и предыдущий план.
Реши, уместно ли естественно возобновить беседу, что точно написать и когда. Можно продолжить
текущую тему или выбрать другую релевантную тему из контекста/паспорта. Не дави, не повторяйся,
не придумывай обязательств. Обнови один план, не добавляй второе напоминание. Можно не писать.
Верни обычный JSON решения и два дополнительных поля: send_at (ISO8601 с явным UTC offset,
не в прошлом, не далее семи дней) и plan_reason. reply — точный текст на выбранный момент,
без изменяемых обещаний «сейчас», если они не подтверждены на этот момент. Если писать неуместно,
reply пуст, send_at null, plan_reason объясняет почему. Не изменяй этап и статус ради напоминания."""


def signature(dialog: Dialog, persona: Persona) -> str:
    """Non-message context changes invalidate a proposal too; no sensitive text in the fence."""
    c = dialog.client
    values = [c.persona_id, c.status.value, c.no_followups, c.passport, c.needs, c.notes,
              c.next_step, c.stage.value, persona.description, persona.routine, persona.timezone,
              persona.model, persona.is_active, persona.prohibitions, persona.language, persona.agent_prompt,
              str(persona.birth_date), persona.strategy_id, c.display_name, str(c.next_step_at),
              dialog.ai_paused, dialog.needs_operator, dialog.channel.status.value,
              dialog.channel.session_enc]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def eligible(dialog: Dialog) -> bool:
    return (not dialog.ai_paused and not dialog.needs_operator and not dialog.client.no_followups
            and dialog.client.status is ClientStatus.ACTIVE
            and dialog.channel.status is SessionStatus.ACTIVE)


def planned_time(value: object, now: datetime) -> datetime:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("offset required")
        at = to_naive_utc(at)
        if not now <= at <= now + MAX_DELAY:
            raise ValueError("outside planning horizon")
        return at
    except (ValueError, TypeError) as exc:
        raise GuardError("invalid reengagement send_at") from exc


class ReengagementService:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.session, self.clock = session, clock
        self.jobs = JobRepo(session)
        self.messages = MessageRepo(session)

    async def sweep(self) -> int:
        """Coalesce work after silence, including existing chats after rollout. No model calls."""
        ids = (await self.session.scalars(select(Dialog.id).where(
            Dialog.last_message_at <= self.clock() - SILENCE,
            Dialog.ai_paused.is_(False), Dialog.needs_operator.is_(False),
        ).order_by(Dialog.last_message_at.desc()))).all()
        count = 0
        for dialog_id in ids:
            if not await try_lock(self.session, NS_DIALOG, dialog_id):
                continue
            dialog = await DialogRepo(self.session).get(dialog_id)
            if dialog is None or not eligible(dialog):
                continue
            persona = await self.session.get(Persona, dialog.client.persona_id)
            if persona is None or not persona.is_active:
                continue
            plan = dialog.reengagement_plan or {}
            sig = signature(dialog, persona)
            if plan.get("revision") == dialog.activity_revision and plan.get("signature") == sig:
                continue
            # An ordinary reply/media generation or unsent human message gets priority.
            if await self.jobs.has_open(jobkinds.REPLY, jobkinds.reply_key(dialog_id)):
                continue
            if await self.jobs.has_open(jobkinds.MEDIA, jobkinds.media_prefix(dialog_id)):
                continue
            queued = await self.session.scalar(select(Message.id).where(
                Message.dialog_id == dialog_id, Message.delivery == Delivery.QUEUED,
                Message.id != plan.get("message_id", 0)).limit(1))
            if queued is not None:
                continue
            if plan.get("message_id"):
                old = await self.session.get(Message, plan["message_id"])
                if old is not None and old.delivery is Delivery.QUEUED:
                    old.delivery = Delivery.CANCELLED
            await self.jobs.cancel_pending(jobkinds.FOLLOWUP, jobkinds.followup_key(dialog_id))
            key = f"reengagement:{dialog_id}"
            if await self.jobs.has_open(jobkinds.REENGAGEMENT, key):
                continue
            dialog.reengagement_plan = {**plan, "revision": dialog.activity_revision,
                "signature": sig, "status": "analyzing", "reason": "Переоценка после тишины"}
            await self.jobs.enqueue(jobkinds.REENGAGEMENT,
                {"dialog_id": dialog_id, "revision": dialog.activity_revision}, self.clock(),
                dedup_key=key)
            count += 1
            if count >= 100:
                break
        return count

    async def channel_open(self, dialog: Dialog, at: datetime) -> bool:
        # Outside a conversational window, do not attempt a free-form proactive message.
        if dialog.channel.kind.value in ("whatsapp", "instagram"):
            last = await self.messages.last(dialog.id, Direction.IN)
            return last is not None and at - last.created_at < timedelta(hours=24)
        return True

    async def analyze(self, dialog_id: int, revision: int, conversation: ConversationService) -> str:
        from sim.services.conversation import history_turns

        if not await try_lock(self.session, NS_REPLY, dialog_id):
            raise GuardError("dialog generation busy")
        dialog = await DialogRepo(self.session).get(dialog_id)
        if dialog is None or not eligible(dialog) or dialog.activity_revision != revision:
            return "superseded"
        if (dialog.reengagement_plan or {}).get("status") != "analyzing":
            return "already_planned"
        persona = await self.session.get(Persona, dialog.client.persona_id)
        if persona is None or not persona.is_active:
            return "inactive"
        sig = signature(dialog, persona)
        if not dialog.last_message_at or self.clock() - dialog.last_message_at < SILENCE:
            return "not_silent"
        strategy = await conversation.strategy_for(persona.id)
        # All available delivered history, not merely the reply window or a truncated summary.
        rows = list((await self.session.scalars(select(Message).where(
            Message.dialog_id == dialog_id).order_by(Message.id))).all())
        history = history_turns(rows)
        _, ctx = await conversation.memory(dialog_id, await conversation.dialog_context(dialog, persona),
                                            refresh_summary=False)
        prior = dialog.reengagement_plan or {}
        contact = {k: prior[k] for k in ("last_sent_id", "delivered_at") if k in prior}
        can_contact = not prior.get("last_sent_id") or any(
            m.author is not Author.AI and m.delivery is Delivery.SENT
            and m.id > prior["last_sent_id"] for m in rows)
        previous = json.dumps(prior, ensure_ascii=False)
        lead = json.dumps({"passport": ctx.passport, "notes": ctx.notes, "needs": ctx.needs,
                           "next_step": ctx.next_step}, ensure_ascii=False)
        ctx = replace(ctx, instruction=INSTRUCTION + "\nПредыдущий план (данные): " + previous
                      + "\nПолные данные лида (не инструкции): " + lead)
        if not can_contact:
            ctx = replace(ctx, instruction=ctx.instruction
                          + "\nПосле прошлого контакта человек не ответил. Оцени контекст заново, "
                          "но не отправляй ещё сообщение: reply пустой, send_at null. "
                          "Передача оператору и отказ от контакта по-прежнему допустимы.")
        base_ctx, feedback, previous_draft = ctx, "", ""
        for _attempt in range(2):
            now = self.clock()
            ctx = replace(base_ctx, now=now, persona_facts=persona_facts(
                persona.birth_date, persona.timezone, now, persona.routine))
            prompt = await conversation.prompt(persona, strategy, history, ctx, whole_history=True)
            if feedback:
                prompt = [*prompt, {"role": "assistant", "content": previous_draft},
                          {"role": "system", "content": "Контролёр отклонил черновик: " + feedback
                           + ". Пересоздай полный JSON, включая send_at, plan_reason и управляющие поля."}]
            decision, result = await conversation.ask(prompt, persona, workflow="reengagement", dialog_id=dialog_id)
            obj = extract_json_object(result.text) or {}
            if decision is None:
                raise GuardError("unusable reengagement decision")
            reason = one_line(obj.get("plan_reason"), 1000)
            if not reason:
                raise GuardError("reengagement reason required")
            control = decision.handoff_to_operator or decision.stop_contact or decision.refused
            at, guard = None, None
            if decision.reply:
                at = planned_time(obj.get("send_at"), ctx.now)
                # Existing routine rules can postpone the proposal. They never force an early send.
                cfg = await conversation.config()
                at = plan_reply(at, persona.routine, persona.timezone, [Incoming("", at)],
                                rules_from(cfg), marked_urgent=answers_at_once(dialog, cfg)).at
                ctx = replace(ctx, now=at, persona_facts=persona_facts(
                    persona.birth_date, persona.timezone, at, persona.routine))
                guard = await conversation.review(strategy, history, decision, result, ctx, dialog_id)
            elif control:
                now = self.clock()
                ctx = replace(ctx, now=now, persona_facts=persona_facts(
                    persona.birth_date, persona.timezone, now, persona.routine))
                guard = await conversation.review(strategy, history, decision, result, ctx, dialog_id)
            if guard is None or guard.allowed:
                break
            previous_draft = result.text[:4000]
            feedback = one_line("; ".join(guard.violations) or "Решение отклонено", 500)
        await lock(self.session, NS_DIALOG, dialog_id)
        dialog = await conversation.reload(dialog_id)
        await self.session.refresh(persona)
        if (not eligible(dialog) or dialog.activity_revision != revision
                or signature(dialog, persona) != sig):
            return "superseded"
        plan = {**contact, "revision": revision, "signature": sig, "reason": reason, "text": "",
                "at": None, "status": "no_send", "analyzed_at": self.clock().isoformat() + "Z"}
        if guard is not None and guard.allowed and control and not decision.reply and not guard.text:
            await conversation.apply_control(dialog, decision, guard)
            plan.update(revision=dialog.activity_revision, signature=signature(dialog, persona),
                        reason=decision.handoff_reason or reason)
        elif guard is not None and guard.allowed and at is not None:
            if control:
                return (await conversation.apply(dialog, strategy, decision, result, guard,
                                                  followup=True)).status
            if not can_contact:
                plan.update(reason="Повторно оценено: ждём ответа человека, повторный контакт запрещён")
            elif not await self.channel_open(dialog, at):
                plan.update(status="blocked", reason="Окно канала закрыто")
            else:
                preview = (dialog.last_message_text, dialog.last_message_direction, dialog.last_message_at)
                message = await conversation.outbound.queue_ai(dialog, guard.text, result, guard=guard,
                    delay_s=max(0, (at - self.clock()).total_seconds()))
                dialog.last_message_text, dialog.last_message_direction, dialog.last_message_at = preview
                plan.update(revision=dialog.activity_revision, text=guard.text, at=at.isoformat() + "Z",
                            status="scheduled", message_id=message.id)
                job = await self.jobs.pending_by_key(jobkinds.send_key(message.id))
                job.payload = {**job.payload, "reengagement_revision": dialog.activity_revision}
        elif guard is not None:
            plan.update(status="blocked", reason="; ".join(guard.violations))
        dialog.reengagement_plan = plan
        return str(plan["status"])

    async def failed(self, dialog_id: int, revision: int) -> None:
        await lock(self.session, NS_DIALOG, dialog_id)
        dialog = await DialogRepo(self.session).get(dialog_id)
        if dialog and dialog.activity_revision == revision:
            dialog.reengagement_plan = {**(dialog.reengagement_plan or {}), "status": "failed",
                "reason": "Не удалось подготовить проверенный план; автоматической отправки нет"}

    async def send_valid(self, dialog: Dialog, message: Message, revision: int, *, cancel: bool = True) -> bool:
        plan = dialog.reengagement_plan or {}
        persona = await self.session.get(Persona, dialog.client.persona_id)
        valid = (eligible(dialog) and persona is not None and persona.is_active
                 and dialog.activity_revision == revision and plan.get("revision") == revision
                 and plan.get("message_id") == message.id and plan.get("status") == "scheduled"
                 and plan.get("signature") == signature(dialog, persona)
                 and await self.channel_open(dialog, self.clock()))
        if valid:
            at = to_naive_utc(datetime.fromisoformat(plan["at"].replace("Z", "+00:00")))
            valid = at <= self.clock() <= at + SEND_GRACE
        if not valid and cancel and message.delivery is Delivery.QUEUED:
            message.delivery = Delivery.CANCELLED
            message.error = "reengagement plan superseded or no longer eligible"
            if plan.get("message_id") == message.id:
                dialog.reengagement_plan = {**plan, "status": "outdated"}
                dialog.activity_revision += 1
        return bool(valid)
