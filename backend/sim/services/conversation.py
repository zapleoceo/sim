"""Conversation engine: dialog -> prompt -> broker -> JSON decision -> funnel + outbound."""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.capabilities import TYPING
from sim.adapters.db.locks import NS_DIALOG, NS_REPLY, lock, try_lock
from sim.adapters.db.models import Dialog, Message, Persona, Strategy
from sim.adapters.db.repositories.catalog import ChannelRepo, PersonaRepo, StrategyRepo
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import Author, ClientStatus, Delivery, Direction, Stage
from sim.domain.funnel import Actor, FunnelRuleError, advance_by_ai
from sim.domain.voice import speaks
from sim.ports.llm import LLMResult
from sim.services import jobkinds
from sim.services.app_settings import AppSettings, AppSettingsService
from sim.services.chat_activity import showing
from sim.services.followup import FollowupService
from sim.services.funnel import FunnelService
from sim.services.guard import BLOCKED, GUARD_WORKFLOW, Guard, GuardOutcome
from sim.services.humanize import bubbles, messenger_touches, typing_pause_s
from sim.services.llm_audit import AuditedLLM
from sim.services.outbound import OutboundService
from sim.services.passport import PASSPORT_DELAY_S
from sim.services.persona_clock import persona_facts
from sim.services.prompt import (
    Decision,
    DialogContext,
    PersonaProfile,
    StrategyProfile,
    Turn,
    build_messages,
    handoff_link,
    one_line,
    parse_decision,
    registration_link,
    stable_system_prompt,
)

log = logging.getLogger(__name__)


REPLY_BUSY_S = 5          # another reply for this dialog is being produced: try again shortly
MEDIA_WAIT_S = 3          # re-check interval while an attachment is still being processed
GUARD_RETRY_TEMPLATE = ("Контролёр отклонил черновик: {feedback}. Пересоздай полный JSON-ответ, "
                        "включая управляющие поля. Не повторяй ошибку.")
NEUTRAL_HANDOFF_REPLY = "Я уточню и вернусь с ответом чуть позже."


class ConversationError(Exception):
    """Misconfiguration (no strategy, missing persona): retrying will not help."""


@dataclass(frozen=True)
class ReplyOutcome:
    status: str  # sent | empty | skipped | needs_operator | waiting_media | busy | superseded
    decision: Decision | None = None
    message: Message | None = None


@dataclass(frozen=True)
class Draft:
    """One generated-and-guarded reply: what the model decided and what the guard made of it.
    Shared by the real dialog flow's siblings (preview, admin simulations)."""

    decision: Decision | None
    result: LLMResult
    guard: GuardOutcome | None = None
    rejection: str = ""


@dataclass(frozen=True)
class PreviewResult:
    reply: str
    decision: Decision | None
    model: str | None
    latency_ms: int
    guard: GuardOutcome | None = None
    context: dict[str, Any] | None = None


def persona_profile(p: Persona) -> PersonaProfile:
    return PersonaProfile(
        name=p.name,
        age=p.age,
        gender=p.gender,
        description=p.description,
        prohibitions=p.prohibitions,
        language=p.language,
        agent_prompt=p.agent_prompt,
        routine=p.routine,
    )


def strategy_profile(s: Strategy) -> StrategyProfile:
    return StrategyProfile(
        name=s.name,
        goal=s.goal,
        prompt=s.prompt,
        offer=s.offer,
        partner_name=s.partner_name,
        registration_url=s.registration_url,
        stage_playbook=dict(s.stage_playbook or {}),
        rules=list(s.rules or []),
    )


def visible_turns(items: Iterable[tuple[bool, str, str | None]]) -> list[Turn]:
    """(is_client, text, guard_verdict) -> turns. A draft the guard blocked was never sent: the
    client did not see it, so it is not part of the conversation. The one rule for every source
    of history (real dialogs, admin simulations)."""
    return [
        Turn("user" if is_client else "assistant", text)
        for is_client, text, verdict in items
        if verdict != BLOCKED
    ]


def history_turns(messages: list[Message]) -> list[Turn]:
    """Client -> user turn; AI and operator -> assistant turn."""
    return visible_turns(
        (m.author is Author.CLIENT, m.text, m.guard_verdict)
        for m in messages
        if m.direction is Direction.IN or m.delivery is Delivery.SENT
    )


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        llm: AuditedLLM,
        outbound: OutboundService,
        *,
        clock: Clock = utcnow,
        settings_env: Mapping[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.app_settings = AppSettingsService(session, settings_env)
        self.cfg: AppSettings | None = None
        self.guard_instance: Guard | None = None
        self.outbound = outbound
        self.clock = clock
        self.dialogs = DialogRepo(session)
        self.messages = MessageRepo(session)
        self.personas = PersonaRepo(session)
        self.strategies = StrategyRepo(session)
        self.jobs = JobRepo(session)
        self.funnel = FunnelService(session)
        self.followups = FollowupService(session, clock=clock)

    # ---- shared pieces -------------------------------------------------------------------
    async def config(self) -> AppSettings:
        """Owner settings, read once per unit of work (a request or a job)."""
        if self.cfg is None:
            self.cfg = await self.app_settings.load()
        return self.cfg

    async def guard(self) -> Guard:
        if self.guard_instance is None:
            cfg = await self.config()
            self.guard_instance = Guard(
                self.llm,
                capability=cfg.guard_capability,
                fallback_capability=cfg.guard_fallback_capability,
                prompt_text=cfg.guard_prompt,
                history_limit=cfg.history_limit,
                model=cfg.guard_model,
            )
        return self.guard_instance

    async def prompt(
        self, persona: Persona, strategy: Strategy, history: list[Turn], ctx: DialogContext
    ) -> list[dict[str, Any]]:
        cfg = await self.config()
        ctx = replace(ctx, persona_facts=persona_facts(
            persona.birth_date, persona.timezone, ctx.now, persona.routine))
        return build_messages(
            persona_profile(persona), strategy_profile(strategy), history, ctx,
            texts=cfg.prompt_texts(), history_limit=cfg.history_limit,
        )

    async def touched(self, guard: GuardOutcome) -> GuardOutcome:
        """The approved text with a person's messenger habits (case, one rare slip), applied by
        code to the whole reply of 1-3 short messages."""
        cfg = await self.config()
        return replace(guard, text=messenger_touches(
            guard.text, typo_percent=cfg.typo_percent, lowercase_percent=cfg.lowercase_percent))

    async def system_prompt(self, persona: Persona) -> str:
        """The exact stable system message this persona sends to its model (for the admin)."""
        cfg = await self.config()
        strategy = await self.strategy_for(persona.id)
        return stable_system_prompt(
            persona_profile(persona), strategy_profile(strategy), cfg.prompt_texts()
        )

    async def strategy_for(self, persona_id: int) -> Strategy:
        persona = await self.personas.get(persona_id)
        strategy = None
        if persona is not None and persona.strategy_id:
            strategy = await self.strategies.get(persona.strategy_id)
        strategy = strategy or await self.strategies.first()
        if strategy is None:
            raise ConversationError("no strategy configured")
        return strategy

    async def handoff_links(self, persona: Persona, channel_id: int, ref_code: str) -> list[tuple[str, str]]:
        """Links to the persona's OTHER active channels that carry the client's ref code."""
        links = []
        for ch in await ChannelRepo(self.session).active_for_persona(persona.id):
            url = handoff_link(ch.kind.value, ch.account_label, ref_code) if ch.id != channel_id else ""
            if url:
                links.append((ch.kind.value, url))
        return links

    async def review(  # noqa: PLR0913
        self,
        strategy: Strategy,
        history: list[Turn],
        decision: Decision,
        result: LLMResult,
        ctx: DialogContext,
        dialog_id: int | None,
        guard_workflow: str = GUARD_WORKFLOW,
    ) -> GuardOutcome:
        """Pre-send guard for one draft. Raises LLMError (fail closed) when it cannot judge."""
        allowed = [
            link
            for link in (registration_link(strategy.registration_url, ctx.ref_code),
                         *(url for _, url in ctx.handoff_links))
            if link
        ]
        return await (await self.guard()).review(
            decision={
                "stage": decision.stage.value if decision.stage else None,
                "needs": decision.needs,
                "next_step": decision.next_step,
                "handoff_to_operator": decision.handoff_to_operator,
                "refused": decision.refused,
                "stop_contact": decision.stop_contact,
            },
            history=history,
            draft=decision.reply,
            stage=ctx.stage,
            generator_model=result.model,
            allowed_links=allowed,
            dialog_id=dialog_id,
            workflow=guard_workflow,
        )

    async def ask(
        self,
        messages: list[dict[str, Any]],
        persona: Persona,
        *,
        workflow: str,
        dialog_id: int | None,
    ) -> tuple[Decision | None, LLMResult]:
        """One model call, plus ONE retry when the JSON is unusable."""
        result = await self.llm.chat(
            messages,
            capability="chat:sales",
            model=persona.model or None,
            json_mode=True,
            workflow=workflow,
            dialog_id=dialog_id,
        )
        decision = self.usable_decision(result, workflow, dialog_id)
        if decision is None:
            log.warning("unusable decision (workflow=%s dialog=%s), retrying", workflow, dialog_id)
            nudge = (await self.config()).prompt_texts().retry_nudge
            retry = [*messages, {"role": "system", "content": nudge}]
            result = await self.llm.chat(
                retry,
                capability="chat:sales",
                model=persona.model or None,
                json_mode=True,
                workflow=workflow,
                dialog_id=dialog_id,
            )
            decision = self.usable_decision(result, workflow, dialog_id)
        return decision, result

    @staticmethod
    def usable_decision(
        result: LLMResult, workflow: str, dialog_id: int | None
    ) -> Decision | None:
        """Parsed decision, or None. An output cut off by the token limit is a parse failure
        even if a prefix happens to parse: the reply would be truncated mid-sentence."""
        if result.finish_reason == "length":
            log.warning(
                "model output truncated (finish_reason=length, workflow=%s dialog=%s)",
                workflow, dialog_id,
            )
            return None
        return parse_decision(result.text)

    # ---- the reply job -------------------------------------------------------------------
    async def run_reply(
        self, dialog_id: int, *, instruction: str = "", workflow: str = "reply"
    ) -> ReplyOutcome:
        """Answer the client (or, with `instruction`, write a follow-up).

        Network/broker errors propagate as LLMError so the job retries with backoff."""
        dialog = await self.dialogs.get(dialog_id)
        if dialog is None:
            return ReplyOutcome("skipped")
        client = dialog.client
        followup = bool(instruction)
        if dialog.ai_paused or client.status is ClientStatus.REFUSED:
            return ReplyOutcome("skipped")
        last = await self.messages.last(dialog_id)
        if last is None or (last.direction is Direction.IN) == followup:
            return ReplyOutcome("skipped")  # nothing to answer / client already replied
        if not await try_lock(self.session, NS_REPLY, dialog_id):
            # One reply at a time per dialog. A customer reply gets another go; a follow-up
            # simply yields (a reply is already on its way).
            if not followup:
                await self.defer_reply(dialog_id, REPLY_BUSY_S)
            return ReplyOutcome("busy")
        if not followup and await self.jobs.has_open(jobkinds.MEDIA, jobkinds.media_prefix(dialog_id)):
            # A voice message is still being downloaded / transcribed: answer once it is text.
            await self.defer_reply(dialog_id, MEDIA_WAIT_S)
            return ReplyOutcome("waiting_media")
        persona = await self.personas.get(client.persona_id)
        if persona is None or not persona.is_active:
            return ReplyOutcome("skipped")
        strategy = await self.strategy_for(persona.id)

        recent = await self.messages.recent_visible(dialog_id, (await self.config()).history_limit)
        cutoff = max((m.id for m in recent if m.direction is Direction.IN), default=0)
        history = history_turns(recent)
        ctx = DialogContext(
            stage=client.stage,
            status=client.status.value,
            client_name=client.display_name,
            channel_kind=dialog.channel.kind.value,
            ref_code=client.ref_code,
            now=self.clock(),
            needs=list(client.needs or []),
            notes=client.notes,
            next_step=client.next_step,
            passport=client.passport or "",
            instruction=instruction,
            handoff_links=await self.handoff_links(persona, dialog.channel_id, client.ref_code),
        )
        # "Марина печатает…" in the chat while the model writes and the guard checks the reply.
        async with showing(self.outbound.try_port(dialog.channel), dialog.external_thread_id, TYPING):
            draft = await self.draft_reply(
                persona, strategy, history, ctx, workflow=workflow, dialog_id=dialog_id
            )
        # The model call took a while. From here on, hold the dialog lock (ingest takes it to
        # insert a client message) and re-read the world before anything is queued.
        await lock(self.session, NS_DIALOG, dialog_id)
        dialog = await self.reload(dialog_id)
        client = dialog.client
        if dialog.ai_paused or client.status is ClientStatus.REFUSED:
            return ReplyOutcome("skipped")  # a human took over / the client refused meanwhile
        newer = await self.messages.last(dialog_id, Direction.IN)
        if newer is not None and newer.id > cutoff:
            # The client wrote again while we were thinking: this answer is already stale. The
            # reply job queued for that message answers with the full context.
            await self.defer_reply(dialog_id, 0)
            return ReplyOutcome("superseded")
        if draft.decision is None or draft.guard is None or not draft.guard.allowed:
            return await self.handoff_failed(dialog, draft)
        # A follow-up answers no client message, so "mirror" never voices it.
        voice = speaks(persona.voice_mode, persona.voice_id,
                       None if followup or newer is None else newer.media_kind)
        return await self.apply(dialog, strategy, draft.decision, draft.result, draft.guard,
                                followup=followup, voice=voice)

    async def handoff_failed(self, dialog: Dialog, draft: Draft) -> ReplyOutcome:
        """Two rejected decisions: one neutral reply, then a visible manager handoff."""
        reason = draft.rejection or "the generator could not produce an approved answer"
        dialog.needs_operator = True
        dialog.ai_paused = True
        await self.followups.cancel(dialog.id)
        await self.jobs.cancel_pending(jobkinds.REPLY, jobkinds.reply_key(dialog.id))
        blocked = draft.guard or GuardOutcome(BLOCKED, draft.decision.reply if draft.decision else "",
                                              [reason])
        await self.outbound.store_blocked(dialog, draft.result, blocked)
        message = await self.outbound.queue_ai(
            dialog, NEUTRAL_HANDOFF_REPLY, None, final=True,
        )
        return ReplyOutcome("needs_operator", draft.decision, message)

    async def defer_reply(self, dialog_id: int, seconds: float) -> None:
        """Make sure ONE reply job is pending, not earlier than `seconds` from now."""
        await self.jobs.upsert_pending(
            jobkinds.REPLY, {"dialog_id": dialog_id},
            self.clock() + timedelta(seconds=seconds), jobkinds.reply_key(dialog_id),
        )

    async def reload(self, dialog_id: int) -> Dialog:
        """The dialog with its client and channel re-read from the database (other
        transactions committed while the model was answering)."""
        stmt = select(Dialog).where(Dialog.id == dialog_id).execution_options(populate_existing=True)
        return (await self.session.scalars(stmt)).unique().one()

    async def apply(
        self,
        dialog: Dialog,
        strategy: Strategy,
        decision: Decision,
        result: LLMResult,
        guard: GuardOutcome | None,
        *,
        followup: bool,
        voice: bool = False,
    ) -> ReplyOutcome:
        if guard is None or not guard.allowed or not decision.reply:
            raise ValueError("only an approved complete decision may change dialog state")
        client = dialog.client
        if decision.needs:
            client.needs = decision.needs
        if decision.next_step:
            client.next_step = decision.next_step
        if decision.stage is not None:
            try:
                await self.funnel.advance(client, decision.stage, Actor.AI, "model decision")
            except FunnelRuleError:
                log.info("ignored forbidden AI stage %s for client %s", decision.stage, client.id)
        stop = decision.refused or decision.stop_contact
        if stop:
            reason = "client asked to stop" if decision.stop_contact else "client refused"
            await self.funnel.set_status(client, ClientStatus.REFUSED, Actor.AI, reason)
            client.no_followups = client.no_followups or decision.stop_contact
            await self.followups.cancel(dialog.id)
        if decision.handoff_to_operator:
            dialog.needs_operator = True
            dialog.ai_paused = True
            await self.followups.cancel(dialog.id)

        decision = replace(decision, reply=guard.text)
        # A voice note is one message; text goes as 1-3 messages with a typing pause between them.
        parts = [guard.text] if voice else bubbles(guard.text)
        delay = 0.0
        for i, part in enumerate(parts):
            if i:
                delay += typing_pause_s(part)
            message = await self.outbound.queue_ai(
                dialog, part, result if i == 0 else None,
                final=stop or decision.handoff_to_operator, guard=guard, voice=voice, delay_s=delay,
            )
        if not followup and not stop and not decision.handoff_to_operator:
            dialog.followup_step = 0
            await self.followups.schedule(dialog, strategy, 0, message)
        # The passportist updates the lead passport (one job per dialog, coalesced).
        await self.jobs.upsert_pending(
            jobkinds.PASSPORT, {"dialog_id": dialog.id},
            self.clock() + timedelta(seconds=PASSPORT_DELAY_S), jobkinds.passport_key(dialog.id),
        )
        return ReplyOutcome("sent", decision, message)

    # ---- drafts shared with preview and admin simulations --------------------------------
    async def draft_reply(  # noqa: PLR0913
        self,
        persona: Persona,
        strategy: Strategy,
        history: list[Turn],
        ctx: DialogContext,
        *,
        workflow: str,
        guard_workflow: str = GUARD_WORKFLOW,
        dialog_id: int | None = None,
    ) -> Draft:
        """The same builder, model call and pre-send guard as production, without any dialog
        bookkeeping. `history` ends with the client's latest message."""
        prompt = await self.prompt(persona, strategy, history, ctx)
        rejection = ""
        for attempt in range(2):
            decision, result = await self.ask(prompt, persona, workflow=workflow, dialog_id=dialog_id)
            guard: GuardOutcome | None = None
            if decision is not None and decision.reply:
                guard = await self.review(
                    strategy, history, decision, result, ctx, dialog_id, guard_workflow
                )
                if guard.allowed:
                    return Draft(decision, result, await self.touched(guard))
                rejection = "; ".join(guard.violations) or "контролёр отклонил решение"
            else:
                rejection = "решение не содержит корректного ответа"
            if attempt == 0:
                feedback = one_line(rejection, 500)
                prompt = [
                    *prompt,
                    {"role": "assistant", "content": result.text[:4000]},
                    {"role": "system", "content":
                     GUARD_RETRY_TEMPLATE.format(feedback=feedback)},
                ]
        return Draft(decision, result, guard, rejection)

    async def preview(
        self, persona: Persona, message: str, history: list[Turn] | None = None,
        *, stage: Stage = Stage.FIRST_CONTACT, status: ClientStatus = ClientStatus.ACTIVE,
        needs: list[str] | None = None, next_step: str = "",
    ) -> PreviewResult:
        """Same builder as production; nothing is persisted or sent."""
        strategy = await self.strategy_for(persona.id)
        ctx = DialogContext(
            stage=stage,
            status=status.value,
            client_name="Preview",
            channel_kind="preview",
            ref_code="PREVIEW",
            now=self.clock(),
            needs=list(needs or []),
            next_step=next_step,
        )
        turns = [*(history or []), Turn("user", message)]
        draft = await self.draft_reply(persona, strategy, turns, ctx, workflow="preview")
        reply = draft.guard.text if draft.guard is not None and draft.guard.allowed else ""
        approved = draft.decision if reply else None
        context = {
            "stage": advance_by_ai(stage, approved.stage).value if approved else stage.value,
            "status": (ClientStatus.REFUSED if approved and (approved.refused or approved.stop_contact)
                       else ClientStatus.PAUSED if approved and approved.handoff_to_operator
                       else status).value,
            "needs": approved.needs if approved and approved.needs else list(needs or []),
            "next_step": approved.next_step if approved and approved.next_step else next_step,
        }
        return PreviewResult(reply, draft.decision, draft.result.model, draft.result.latency_ms,
                             draft.guard, context)

