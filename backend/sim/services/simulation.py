"""Admin simulations: a test conversation with a persona that lives only in sim_branch /
sim_message. No client, dialog or message rows exist for it, so it never reaches the inbox,
the funnel or the analytics, and nothing is sent to a real channel.

The conversation engine is shared, not copied: history goes through `visible_turns`, the prompt
through `ConversationService.draft_reply` (prompt builder + broker call + decision parsing +
pre-send guard) and the funnel rules through `domain.funnel.advance_by_ai`. Only the state
bookkeeping differs: it is kept on the branch instead of a client row."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AdminUser, Persona, SimBranch, SimMessage
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.simulations import SimBranchRepo, SimMessageRepo, SimPrefRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus, Stage
from sim.domain.funnel import advance_by_ai
from sim.services.conversation import ConversationService, visible_turns
from sim.services.guard import BLOCKED, REWRITTEN
from sim.services.prompt import MAX_NEEDS, Decision, DialogContext

SIM_WORKFLOW = "sim"
SIM_GUARD_WORKFLOW = "sim_guard"
CLIENT, AI = "client", "ai"
CLIENT_NAME_PREFIX = "Тест "
BRANCH_LIST_LIMIT = 10


def sim_ref(branch_id: int) -> str:
    """The fake ref code of a simulated client: its registration link is never a real client's."""
    return f"SIM{branch_id}"


def client_name_for(first_name: str | None) -> str:
    return f"{CLIENT_NAME_PREFIX}{(first_name or '').strip() or 'админ'}"[:60]


@dataclass(frozen=True)
class SimReply:
    # sent | blocked | empty | no_decision | skipped
    status: str
    text: str = ""                 # what the persona said (the blocked draft when blocked)
    verdict: str | None = None     # ok | rewritten | blocked
    violations: list[str] = field(default_factory=list)
    model: str | None = None
    stage: Stage | None = None
    note: str = ""                 # why a reply was skipped


class SimulationService:
    def __init__(
        self, session: AsyncSession, conversation: ConversationService, *, clock: Clock = utcnow
    ) -> None:
        self.session = session
        self.conversation = conversation
        self.clock = clock
        self.branches = SimBranchRepo(session)
        self.messages = SimMessageRepo(session)
        self.prefs = SimPrefRepo(session)
        self.personas = PersonaRepo(session)

    # ---- branches ------------------------------------------------------------------------
    async def start(self, admin: AdminUser, persona: Persona) -> SimBranch:
        now = self.clock()
        branch = await self.branches.add(
            SimBranch(
                admin_user_id=admin.id,
                persona_id=persona.id,
                title=f"{persona.name} · {now:%d.%m %H:%M}",
                created_at=now,
                last_active_at=now,
            )
        )
        return branch

    async def resume(self, admin: AdminUser, branch_id: int) -> SimBranch | None:
        """Make an earlier branch the current one (a closed branch is reopened)."""
        branch = await self.branches.get(branch_id)
        if branch is None or branch.admin_user_id != admin.id:
            return None
        branch.closed_at = None
        branch.last_active_at = self.clock()
        return branch

    async def close(self, branch: SimBranch) -> None:
        branch.closed_at = self.clock()

    async def add_client_message(self, branch: SimBranch, text: str) -> SimMessage:
        now = self.clock()
        branch.last_active_at = now
        return await self.messages.add(SimMessage(branch_id=branch.id, role=CLIENT, text=text, created_at=now))

    # ---- the reply -----------------------------------------------------------------------
    async def reply(self, branch: SimBranch, client_name: str) -> SimReply:
        """Answer the client's latest message. LLMError propagates (the job retries)."""
        persona = branch.persona
        if branch.closed_at is not None:
            return SimReply("skipped", note="ветка закрыта")
        if branch.status is ClientStatus.REFUSED:
            return SimReply("skipped", note="клиент отказался: ИИ больше не пишет")
        if branch.status is ClientStatus.PAUSED:
            return SimReply("skipped", note="диалог передан оператору: ИИ на паузе")
        if not persona.is_active:
            return SimReply("skipped", note="персонаж выключен")
        strategy = await self.conversation.strategy_for(persona.id)
        rows = await self.messages.recent(branch.id, (await self.conversation.config()).history_limit)
        history = visible_turns((m.role == CLIENT, m.text, m.guard_verdict) for m in rows)
        ref = sim_ref(branch.id)
        ctx = DialogContext(
            stage=branch.stage,
            status=branch.status.value,
            client_name=client_name,
            channel_kind="telegram",
            ref_code=ref,
            now=self.clock(),
            needs=list(branch.needs or []),
            notes=branch.notes,
            next_step=branch.next_step,
            handoff_links=await self.conversation.handoff_links(persona, 0, ref),
        )
        draft = await self.conversation.draft_reply(
            persona, strategy, history, ctx, workflow=SIM_WORKFLOW, guard_workflow=SIM_GUARD_WORKFLOW
        )
        guard = draft.guard
        if draft.decision is None or guard is None:
            return SimReply("no_decision", model=draft.result.model, stage=branch.stage,
                            note=draft.rejection)
        blocked = not guard.allowed
        if not blocked:
            self.apply_decision(branch, draft.decision)
        await self.messages.add(
            SimMessage(
                branch_id=branch.id,
                role=AI,
                text=guard.text,
                guard_verdict=guard.verdict,
                guard_violations=list(guard.violations) or None,
                original_text=guard.original_text if guard.verdict == REWRITTEN else None,
                model=draft.result.model,
                created_at=self.clock(),
            )
        )
        branch.last_active_at = self.clock()
        return SimReply(
            BLOCKED if blocked else "sent",
            text=guard.text,
            verdict=guard.verdict,
            violations=list(guard.violations),
            model=draft.result.model,
            stage=branch.stage,
        )

    @staticmethod
    def apply_decision(branch: SimBranch, decision: Decision) -> None:
        """The in-branch twin of ConversationService.apply: same funnel rules, no client row."""
        if decision.needs:
            branch.needs = decision.needs[:MAX_NEEDS]
        if decision.next_step:
            branch.next_step = decision.next_step
        branch.stage = advance_by_ai(branch.stage, decision.stage)
        if decision.refused or decision.stop_contact:
            branch.status = ClientStatus.REFUSED
        elif decision.handoff_to_operator:
            branch.status = ClientStatus.PAUSED
            branch.notes = (branch.notes + "\nпередано оператору").strip()
