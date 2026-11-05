"""Conversation engine: dialog -> prompt -> broker -> JSON decision -> funnel + outbound."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Dialog, Message, Persona, Strategy
from sim.adapters.db.repositories.catalog import PersonaRepo, StrategyRepo
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import Author, ClientStatus, Direction, Stage
from sim.domain.funnel import Actor, FunnelRuleError
from sim.ports.llm import LLMResult
from sim.services.followup import FollowupService
from sim.services.funnel import FunnelService
from sim.services.llm_audit import AuditedLLM
from sim.services.outbound import OutboundService
from sim.services.prompt import (
    HISTORY_LIMIT,
    Decision,
    DialogContext,
    PersonaProfile,
    StrategyProfile,
    Turn,
    build_messages,
    parse_decision,
)

log = logging.getLogger(__name__)

RETRY_NUDGE = (
    "Your previous output was not a valid JSON object. Reply again with ONLY the JSON object "
    "described in OUTPUT FORMAT."
)


class ConversationError(Exception):
    """Misconfiguration (no strategy, missing persona): retrying will not help."""


@dataclass(frozen=True)
class ReplyOutcome:
    status: str  # sent | empty | skipped | needs_operator
    decision: Decision | None = None
    message: Message | None = None


@dataclass(frozen=True)
class PreviewResult:
    reply: str
    decision: Decision | None
    model: str | None
    latency_ms: int


def persona_profile(p: Persona) -> PersonaProfile:
    return PersonaProfile(
        name=p.name,
        age=p.age,
        gender=p.gender,
        tagline=p.tagline,
        character=p.character,
        backstory=p.backstory,
        communication_style=p.communication_style,
        language=p.language,
        disclosure_text=p.disclosure_text,
    )


def strategy_profile(s: Strategy) -> StrategyProfile:
    return StrategyProfile(
        name=s.name,
        goal=s.goal,
        offer=s.offer,
        partner_name=s.partner_name,
        registration_url=s.registration_url,
        stage_playbook=dict(s.stage_playbook or {}),
        rules=list(s.rules or []),
    )


def history_turns(messages: list[Message]) -> list[Turn]:
    """Client -> user turn; AI and operator -> assistant turn."""
    return [
        Turn("user" if m.author is Author.CLIENT else "assistant", m.text) for m in messages
    ]


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        llm: AuditedLLM,
        outbound: OutboundService,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self.session = session
        self.llm = llm
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
    async def strategy_for(self, persona_id: int) -> Strategy:
        persona = await self.personas.get(persona_id)
        strategy = None
        if persona is not None and persona.strategy_id:
            strategy = await self.strategies.get(persona.strategy_id)
        strategy = strategy or await self.strategies.first()
        if strategy is None:
            raise ConversationError("no strategy configured")
        return strategy

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
        decision = parse_decision(result.text)
        if decision is None:
            log.warning("unparsable decision (workflow=%s dialog=%s), retrying", workflow, dialog_id)
            retry = [*messages, {"role": "system", "content": RETRY_NUDGE}]
            result = await self.llm.chat(
                retry,
                capability="chat:sales",
                model=persona.model or None,
                json_mode=True,
                workflow=workflow,
                dialog_id=dialog_id,
            )
            decision = parse_decision(result.text)
        return decision, result

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
        persona = await self.personas.get(client.persona_id)
        if persona is None or not persona.is_active:
            return ReplyOutcome("skipped")
        strategy = await self.strategy_for(persona.id)

        history = history_turns(await self.messages.recent(dialog_id, HISTORY_LIMIT))
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
            instruction=instruction,
        )
        prompt = build_messages(persona_profile(persona), strategy_profile(strategy), history, ctx)
        decision, result = await self.ask(prompt, persona, workflow=workflow, dialog_id=dialog_id)
        if decision is None:
            dialog.needs_operator = True
            return ReplyOutcome("needs_operator")
        return await self.apply(dialog, strategy, decision, result, followup=followup)

    async def apply(
        self,
        dialog: Dialog,
        strategy: Strategy,
        decision: Decision,
        result: LLMResult,
        *,
        followup: bool,
    ) -> ReplyOutcome:
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

        if not decision.reply:
            return ReplyOutcome("empty", decision)
        message = await self.outbound.queue_ai(
            dialog, decision.reply, result, list(strategy.reply_delay_s or [])
        )
        if not followup and not stop and not decision.handoff_to_operator:
            dialog.followup_step = 0
            await self.followups.schedule(dialog, strategy, 0, message)
        return ReplyOutcome("sent", decision, message)

    # ---- preview -------------------------------------------------------------------------
    async def preview(
        self, persona: Persona, message: str, history: list[Turn] | None = None
    ) -> PreviewResult:
        """Same builder as production; nothing is persisted or sent."""
        strategy = await self.strategy_for(persona.id)
        ctx = DialogContext(
            stage=Stage.FIRST_CONTACT,
            status=ClientStatus.ACTIVE.value,
            client_name="Preview",
            channel_kind="preview",
            ref_code="PREVIEW",
            now=self.clock(),
        )
        turns = [*(history or []), Turn("user", message)]
        prompt = build_messages(persona_profile(persona), strategy_profile(strategy), turns, ctx)
        decision, result = await self.ask(prompt, persona, workflow="preview", dialog_id=None)
        reply = decision.reply if decision else result.text[:2000]
        return PreviewResult(reply, decision, result.model, result.latency_ms)
