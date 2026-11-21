"""The phone check (docs/attention.md): task `agent.phone_check(persona_id)` ->
`PhoneCheck.run(persona_id)`.

1. Look (transaction): take the due check (`schedule.claim`, a stale job stops here); persona
   archived or AI off — nothing; collect what is new in all her chats (`notices`); nothing —
   done; she is away (asleep, running…) — the next look goes to the end of that event.
2. Decide (no transaction): the rules, then the attention agent for the rest (`decide`,
   `agent`); every chat gets exactly one decision.
3. Act (transaction): one `attention` event per chat with the reason (hidden from the reply
   agent, except the note about a missed call), the lease is given back and the earliest
   promised answer becomes the next check. After commit: reply turns, read receipts, the
   next check's job. The reply turn itself is unchanged (docs/reply.md)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from sim.db.models import Persona
from sim.db.session import transaction
from sim.domain.attention import AttentionSettings, attention_settings
from sim.domain.enums import EventKind
from sim.domain.life import BusyLevel
from sim.events import append_event
from sim.services.attention.agent import AttentionAgent
from sim.services.attention.decide import (
    READS_NOW,
    REPLYING,
    Decision,
    agent_input,
    decision_data,
    from_agent,
    rule_decision,
)
from sim.services.attention.notices import ChatNotice, collect_notices
from sim.services.attention.schedule import claim, queue_check, release, request_check
from sim.services.attention.timing import Availability, availability, check_delay
from sim.services.deps import Deps
from sim.services.life.store import load_life
from sim.services.presence import mark_read

log = logging.getLogger(__name__)

ACTOR = "persona"
SKIPPED_CHAT = "«Внимание» не решило по этому чату"


class CheckStatus(StrEnum):
    NOT_DUE = "not_due"         # a stale job: the check was moved or already taken
    OFF = "off"                 # archived or AI off
    NOTHING = "nothing"         # nothing new in her chats
    AWAY = "away"               # she cannot look now: the next check is at the event's end
    DONE = "done"


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    decisions: tuple[Decision, ...] = ()
    next_check: datetime | None = None


@dataclass
class _Look:
    persona: Persona
    settings: AttentionSettings
    av: Availability
    lease: datetime
    notices: list[ChatNotice] = field(default_factory=list)


def _last_message(notice: ChatNotice) -> int | None:
    """The newest unanswered client message (the read receipt goes up to it)."""
    return notice.unanswered.last_in_message_id if notice.unanswered is not None else None


class PhoneCheck:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.agent = AttentionAgent(deps.llm, deps.sessions)

    async def run(self, persona_id: int) -> CheckResult:
        now = self.deps.clock.now()
        looked = await self._look(persona_id, now)
        if isinstance(looked, CheckResult):
            await queue_check(self.deps.tasks, persona_id, looked.next_check, now)
            return looked
        clear, undecided = self._by_rules(looked, now)
        started: list[Decision] = []
        if undecided:
            # Clear chats («Отвечать сразу», a live exchange, a promise come due) do not wait for
            # the agent's answer about other chats: their turns start at once.
            started = [d for d in clear if d.later_at is None]
            if started:
                await self._dispatch(looked, started)
            clear = [d for d in clear if d.later_at is not None]
        decisions = clear + await self._by_agent(looked, undecided, now)
        result = await self._act(looked, decisions, now)
        return replace(result, decisions=(*started, *result.decisions))

    # --- 1. look ---------------------------------------------------------------------------

    async def _look(self, persona_id: int, now: datetime) -> _Look | CheckResult:
        async with transaction(self.deps.sessions) as session:
            lease = await claim(session, persona_id, now)
            if lease is None:
                return CheckResult(CheckStatus.NOT_DUE)
            persona = await session.get(Persona, persona_id)
            if persona is None or not persona.is_active or not persona.ai_enabled:
                await release(session, persona_id, lease)
                return CheckResult(CheckStatus.OFF)
            settings = attention_settings(persona.attention)
            av = availability(await load_life(session, persona, now), settings)
            notices = await collect_notices(session, persona, now, settings)
            if not notices:
                await release(session, persona_id, lease)
                return CheckResult(CheckStatus.NOTHING)
            if av.level == BusyLevel.AWAY:
                await release(session, persona_id, lease)
                due = now + timedelta(seconds=check_delay(av, now, settings.pace,
                                                          self.deps.uniform()))
                accepted = await request_check(session, persona_id, due, now)
                return CheckResult(CheckStatus.AWAY, next_check=due if accepted else None)
            return _Look(persona=persona, settings=settings, av=av, lease=lease,
                         notices=notices)

    # --- 2. decide -------------------------------------------------------------------------

    def _by_rules(self, look: _Look, now: datetime) -> tuple[list[Decision], list[ChatNotice]]:
        decisions: list[Decision] = []
        undecided: list[ChatNotice] = []
        for notice in look.notices:
            rule = rule_decision(notice, look.av, look.settings, now, look.persona.timezone)
            if rule is not None:
                decisions.append(rule)
            else:
                undecided.append(notice)
        return decisions, undecided

    async def _dispatch(self, look: _Look, decisions: list[Decision]) -> None:
        """Record and start clear decisions now, keeping the check's lease."""
        notices = {notice.chat_id: notice for notice in look.notices}
        replies, reads = [], []
        async with transaction(self.deps.sessions) as session:
            for decision in decisions:
                notice = notices[decision.chat_id]
                await append_event(session, chat_id=decision.chat_id, kind=EventKind.ATTENTION,
                                   actor=ACTOR, in_context=bool(decision.note),
                                   data=decision_data(decision, notice, look.persona))
                if decision.action in REPLYING:
                    replies.append(decision.chat_id)
                elif decision.action in READS_NOW and (last := _last_message(notice)):
                    reads.append(last)
        for chat_id in replies:
            await self.deps.tasks.reply(chat_id, 0)
        for message_id in reads:
            await mark_read(self.deps.tasks, message_id)

    async def _by_agent(self, look: _Look, undecided: list[ChatNotice],
                        now: datetime) -> list[Decision]:
        tz = look.persona.timezone
        decisions: list[Decision] = []
        if not undecided:
            return decisions
        answer = await self.agent.decide(
            look.persona, agent_input(look.persona, look.av, undecided, now, look.settings))
        plan = answer.plan
        chosen = {d.chat_id: d for d in plan.decisions} if plan is not None else {}
        for notice in undecided:
            decisions.append(from_agent(
                notice, chosen.get(notice.chat_id), av=look.av, settings=look.settings,
                now=now, tz=tz, u=self.deps.uniform(), missing=answer.error or SKIPPED_CHAT))
        return decisions

    # --- 3. act ----------------------------------------------------------------------------

    async def _act(self, look: _Look, decisions: list[Decision], now: datetime) -> CheckResult:
        notices = {notice.chat_id: notice for notice in look.notices}
        replies: list[int] = []
        reads: list[int] = []
        promised: list[datetime] = []
        async with transaction(self.deps.sessions) as session:
            for decision in decisions:
                notice = notices[decision.chat_id]
                await append_event(session, chat_id=decision.chat_id, kind=EventKind.ATTENTION,
                                   actor=ACTOR, in_context=bool(decision.note),
                                   data=decision_data(decision, notice, look.persona))
                if decision.action in REPLYING:
                    replies.append(decision.chat_id)        # the turn marks the chat read
                elif decision.action in READS_NOW and (last := _last_message(notice)):
                    reads.append(last)
                if decision.later_at is not None:
                    promised.append(decision.later_at)
            await release(session, look.persona.id, look.lease)
            due = min(promised) if promised else None
            if due is not None and not await request_check(session, look.persona.id, due, now):
                due = None
        for chat_id in replies:
            await self.deps.tasks.reply(chat_id, 0)
        for message_id in reads:
            await mark_read(self.deps.tasks, message_id)
        await queue_check(self.deps.tasks, look.persona.id, due, now)
        log.info("phone check persona=%s: %s", look.persona.id,
                 ", ".join(f"{d.chat_id}:{d.action.value}" for d in decisions))
        return CheckResult(CheckStatus.DONE, tuple(decisions), next_check=due)
