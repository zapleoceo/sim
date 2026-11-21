"""One persona turn (docs/reply.md): task `agent.reply_chat` -> `ReplyService.run(chat_id)`.

1. Start (transaction, chat row locked): skip when AI is off anywhere, the chat waits for an
   operator or nothing is unanswered; wait for a burst of messages (`persona.reply_delay_s`)
   and for attachments still being understood (up to 90 s) by re-queueing the reply; otherwise
   create `reply_run` with the context snapshot. WHEN a turn starts at all is decided before
   it: by her phone check (docs/attention.md), or at once with «Всегда сразу». The chat is
   marked read up to the newest client message and the client sees «печатает» while the
   model thinks. A day without a plan
   asks the daily planner for one (task `agent.plan_day`).
2. Decide (no transaction): the reply model returns a JSON plan (one repair retry when the
   JSON or the media choice is invalid). A plan with `recall` and no messages («а помнишь…?»)
   makes code look the days up (sim/services/memory/recall.py) and ask the model again with a
   «Вспомнила» block — once per turn. The guard checks the plan; on a block the model tries
   once more with the reason; a second block -> run blocked, chat needs an operator.
3. Prepare (no transaction): slow per-action work (TTS, cutting a video to a circle), each
   announced by its Telegram status («записывает голосовое», «записывает кружок»...).
4. Commit (transaction, chat row locked): if a newer client message arrived meanwhile the plan
   is dropped (run skipped) and the reply is re-queued; else pending messages / generations
   are written in plan order, `canon` facts about herself go to `persona_canon`, the plan's
   `remember` facts go to today's «заметки дня» (`chat.day_notes`, seen by the next turn at
   once; the passport is updated at night, docs/memory.md), and after commit the messages are
   queued for sending and the generations for running. A turn that queues nothing (silence,
   block, failure, dropped plan) cancels the status."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Client, Persona, ReplyRun
from sim.db.session import transaction
from sim.domain.agents import AgentKey, agent_config
from sim.domain.ai_gate import AiBlock, ai_block
from sim.domain.enums import EventKind, LlmPurpose, RunStatus
from sim.domain.memory import with_notes
from sim.domain.plan import ReplyPlan
from sim.domain.presence import THINKING
from sim.events import append_event
from sim.ports import ChatMessage, ChatPart, PortError
from sim.services.actions import ActionContext, ApplyResult, handler_for
from sim.services.context.builder import ContextBuilder, ReplyContext, speakers_of
from sim.services.context.facts import to_local
from sim.services.context.window import local_day
from sim.services.deps import Deps
from sim.services.guard import Guard
from sim.services.life.canon import add_canon
from sim.services.life.store import load_days
from sim.services.llm_log import describe_messages, logged_call
from sim.services.memory.recall import Recollection, recollect
from sim.services.plan_check import PlanError, validated_plan
from sim.services.presence import mark_read, show_busy, stop_busy
from sim.services.turn_state import Unanswered, newer_client_message, unanswered

log = logging.getLogger(__name__)

UNDERSTANDING_WAIT_S = 90.0     # longest wait for attachments before answering anyway
UNDERSTANDING_POLL_S = 5.0
DEBOUNCE_SLACK_S = 0.5
PLAN_ROUNDS = 2                 # first answer + one retry after a guard block
PLAN_SCHEMA = ReplyPlan.model_json_schema()

REPAIR_NOTE = ("Ответ не подошёл: {errors}. Верни исправленный JSON-план строго по схеме, "
               "без текста вокруг.")
BLOCKED_NOTE = ("Проверка не пропустила этот ответ: {reason}. Напиши другой ответ без этой "
                "ошибки, снова JSON-планом.")
RECALLED_NOTE = ("recall: ты уже вспоминала в этом ходе — ответь собеседнику сообщениями "
                 "по тому, что вспомнила")


@dataclass(frozen=True)
class TurnResult:
    """What the turn did. `status` None = no run was created (skipped or re-queued)."""

    status: RunStatus | None
    run_id: int | None = None
    reason: str = ""
    requeue_s: float | None = None
    outgoing: int = 0               # messages + generations queued by a sent turn


@dataclass
class _Turn:
    run_id: int
    chat_id: int
    persona: Persona
    ctx: ReplyContext
    last_in_message_id: int | None = None
    plan_today: date | None = None          # today has no plan: ask the planner for it
    recall: Recollection | None = None      # what she looked up this turn (once)


@dataclass
class _Decision:
    plan: ReplyPlan
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    blocked: bool = False


def _message(role: str, text: str) -> ChatMessage:
    return ChatMessage(role=role, parts=[ChatPart(text=text)])


class ReplyService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.builder = ContextBuilder(deps.clock, deps.settings)
        self.guard = Guard(deps.llm, deps.sessions)

    async def run(self, chat_id: int) -> TurnResult:
        started = await self._start(chat_id)
        if isinstance(started, TurnResult):
            if started.requeue_s is not None:
                await self.deps.tasks.reply(chat_id, started.requeue_s)
            return started
        turn = started
        if turn.plan_today is not None:
            # This turn uses the routine; the next ones get the planned day.
            await self.deps.tasks.plan_day(turn.persona.id, turn.plan_today.isoformat())
        # The persona "reads" the chat and starts typing while the model thinks.
        await mark_read(self.deps.tasks, turn.last_in_message_id)
        await show_busy(self.deps.tasks, turn.chat_id, THINKING)
        try:
            result = await self._handle(turn)
        except Exception:
            await stop_busy(self.deps.tasks, turn.chat_id)
            raise
        if not result.outgoing:
            # Silence, a block, a failure or a dropped plan: no message will end the status.
            await stop_busy(self.deps.tasks, turn.chat_id)
        return result

    async def _handle(self, turn: _Turn) -> TurnResult:
        try:
            decision = await self._decide(turn)
            if decision.blocked:
                await self._block(turn, decision)
                return TurnResult(RunStatus.BLOCKED, turn.run_id, "guard blocked twice")
            prepared = await self._prepare(turn, decision.plan)
            return await self._commit(turn, decision, prepared)
        except PlanError as exc:
            await self._fail(turn, f"план не прошёл проверку: {exc}")
            return TurnResult(RunStatus.FAILED, turn.run_id, str(exc))
        except PortError as exc:
            await self._fail(turn, str(exc) or type(exc).__name__)
            if exc.retryable:
                raise
            return TurnResult(RunStatus.FAILED, turn.run_id, str(exc))
        except Exception as exc:
            await self._fail(turn, f"внутренняя ошибка: {exc!r}")
            raise

    # --- 1. start -------------------------------------------------------------------------

    async def _start(self, chat_id: int) -> _Turn | TurnResult:
        async with transaction(self.deps.sessions) as session:
            chat = await session.get(Chat, chat_id, with_for_update=True)
            if chat is None:
                return TurnResult(None, reason="chat not found")
            persona = await session.get(Persona, chat.persona_id)
            client = await session.get(Client, chat.client_id)
            blocked = ai_block(persona, client, chat)
            if blocked == AiBlock.NEEDS_OPERATOR:
                return TurnResult(None, reason="waiting for an operator")
            if blocked is not None or persona is None:
                return TurnResult(None, reason="AI is off")
            waiting = await unanswered(session, chat_id)
            if waiting is None:
                return TurnResult(None, reason="nothing to answer")
            now = self.deps.clock.now()
            wait_left = self._burst_left(persona, waiting, now)
            if wait_left > DEBOUNCE_SLACK_S:
                return TurnResult(None, reason="client is still typing", requeue_s=wait_left)
            understanding_s = None
            if waiting.pending_since is not None:
                understanding_s = (now - waiting.pending_since).total_seconds()
            if understanding_s is not None and understanding_s < UNDERSTANDING_WAIT_S:
                return TurnResult(None, reason="attachments are being understood",
                                  requeue_s=UNDERSTANDING_POLL_S)
            run = ReplyRun(chat_id=chat_id, status=RunStatus.RUNNING,
                           trigger_event_id=waiting.last_in_event_id, attempts=0)
            session.add(run)
            if not agent_config(persona, AgentKey.REPLY).model:
                run.status = RunStatus.FAILED
                run.error = "не выбрана модель ответов"
                run.finished_at = now
                await session.flush()
                return TurnResult(RunStatus.FAILED, run.id, run.error)
            ctx = await self.builder.for_reply(session, chat_id)
            run.context = ctx.snapshot
            await session.flush()
            return _Turn(run_id=run.id, chat_id=chat_id, persona=persona, ctx=ctx,
                         last_in_message_id=waiting.last_in_message_id,
                         plan_today=await self._unplanned_today(session, persona, now))

    @staticmethod
    def _burst_left(persona: Persona, waiting: Unanswered, now: datetime) -> float:
        """Seconds until the client has finished a burst of messages (`reply_delay_s` after
        the newest one)."""
        burst_until = waiting.last_in_at + timedelta(seconds=persona.reply_delay_s)
        return (burst_until - now).total_seconds()

    @staticmethod
    async def _unplanned_today(session: AsyncSession, persona: Persona,
                               now: datetime) -> date | None:
        """Today's local date when the planner is on and today has no plan yet."""
        if not agent_config(persona, AgentKey.LIFE).model:
            return None
        today = to_local(now, persona.timezone).date()
        rows = await load_days(session, persona.id, today, today)
        return None if rows.get(today) is not None else today

    # --- 2. decide ------------------------------------------------------------------------

    async def _decide(self, turn: _Turn) -> _Decision:
        messages = list(turn.ctx.messages)
        decision: _Decision | None = None
        for attempt in range(1, PLAN_ROUNDS + 1):
            plan, raw = await self._plan(turn, messages)
            if plan.wants_recall and turn.recall is None:
                messages = [*messages, _message("assistant", raw),
                            _message("user", await self._recall(turn, plan))]
                await show_busy(self.deps.tasks, turn.chat_id, THINKING)
                plan, raw = await self._plan(turn, messages)
            if decision is None:
                decision = _Decision(plan=plan)
            decision.plan, decision.attempts = plan, attempt
            if not plan.messages:
                return decision         # silence needs no check
            verdict = await self.guard.check(
                persona=turn.persona, facts=turn.ctx.facts, client_lines=turn.ctx.client_lines,
                plan=plan, chat_id=turn.chat_id, reply_run_id=turn.run_id,
            )
            decision.verdicts.append({**verdict.model_dump(), "attempt": attempt})
            if verdict.ok:
                return decision
            messages = [*messages, _message("assistant", raw),
                        _message("user", BLOCKED_NOTE.format(reason=verdict.reason))]
        assert decision is not None
        decision.blocked = True
        return decision

    async def _plan(self, turn: _Turn, messages: list[ChatMessage]) -> tuple[ReplyPlan, str]:
        raw = await self._call_model(turn, messages)
        try:
            return self._validate(turn, raw), raw
        except PlanError as exc:
            log.info("chat %s run %s: invalid plan, repairing: %s", turn.chat_id, turn.run_id,
                     exc)
            repair = [*messages, _message("assistant", raw),
                      _message("user", REPAIR_NOTE.format(errors="; ".join(exc.errors)))]
            raw = await self._call_model(turn, repair)
            return self._validate(turn, raw), raw

    def _validate(self, turn: _Turn, raw: str) -> ReplyPlan:
        plan = validated_plan(raw, catalog=turn.ctx.catalog, caps=turn.ctx.capabilities)
        if plan.wants_recall and turn.recall is not None:
            raise PlanError([RECALLED_NOTE])
        return plan

    async def _recall(self, turn: _Turn, plan: ReplyPlan) -> str:
        """«А помнишь…?»: the days she looks up, as the «Вспомнила» block."""
        assert plan.recall is not None
        async with transaction(self.deps.sessions) as session:
            chat = await session.get(Chat, turn.chat_id)
            client = await session.get(Client, chat.client_id) if chat is not None else None
            if client is None:
                raise PlanError(["чат пропал"])
            turn.recall = await recollect(session, turn.chat_id, plan.recall,
                                          speakers_of(turn.persona, client),
                                          self.deps.clock.now())
        return turn.recall.text

    async def _call_model(self, turn: _Turn, messages: list[ChatMessage]) -> str:
        cfg = agent_config(turn.persona, AgentKey.REPLY)
        request: dict[str, Any] = {"messages": describe_messages(messages),
                                   "json_schema": "ReplyPlan"}
        if turn.recall is not None:
            request["recall"] = turn.recall.request     # the second call, after «Вспомнила»
        result = await logged_call(
            self.deps.sessions, purpose=LlmPurpose.REPLY, model=cfg.model,
            request=request,
            call=lambda: self.deps.llm.chat(model=cfg.model, messages=messages,
                                            json_schema=PLAN_SCHEMA, max_tokens=cfg.max_tokens,
                                            label="reply", key_tier=cfg.key_tier),
            chat_id=turn.chat_id, reply_run_id=turn.run_id, key_tier=cfg.key_tier,
        )
        return result.text

    # --- 3. prepare -----------------------------------------------------------------------

    def _action_context(self, turn: _Turn) -> ActionContext:
        return ActionContext(deps=self.deps, persona=turn.persona, chat_id=turn.chat_id,
                             reply_run_id=turn.run_id)

    async def _prepare(self, turn: _Turn, plan: ReplyPlan) -> list[Any]:
        actx = self._action_context(turn)
        prepared = []
        for action in plan.messages:
            handler = handler_for(action)
            busy = handler.busy(action)
            if busy is not None:            # «записывает голосовое» before TTS, etc.
                await show_busy(self.deps.tasks, turn.chat_id, busy)
            prepared.append(await handler.prepare(action, actx))
        return prepared

    # --- 4. commit ------------------------------------------------------------------------

    async def _commit(self, turn: _Turn, decision: _Decision, prepared: list[Any]) -> TurnResult:
        result = ApplyResult()
        requeue = False
        async with transaction(self.deps.sessions) as session:
            chat = await session.get(Chat, turn.chat_id, with_for_update=True)
            run = await session.get(ReplyRun, turn.run_id)
            if chat is None or run is None:
                return TurnResult(None, turn.run_id, "chat or run disappeared")
            persona = await session.get(Persona, chat.persona_id)
            client = await session.get(Client, chat.client_id)
            self._record(run, decision, turn.recall)
            skip_reason = ""
            if await newer_client_message(session, chat.id, run.trigger_event_id or 0):
                skip_reason, requeue = "пришло новое сообщение, ответ пересобран", True
            elif ai_block(persona, client, chat) is not None:
                skip_reason = "ИИ выключили, пока готовился ответ"
            if skip_reason:
                run.status = RunStatus.SKIPPED
                run.error = skip_reason
                run.finished_at = self.deps.clock.now()
            else:
                await self._apply(session, chat, turn, decision, prepared, result, run)
        if requeue and persona is not None:
            await self.deps.tasks.reply(turn.chat_id, float(persona.reply_delay_s))
            return TurnResult(RunStatus.SKIPPED, turn.run_id, "newer client message")
        if run.status == RunStatus.SKIPPED:
            return TurnResult(RunStatus.SKIPPED, turn.run_id, run.error or "")
        for message_id in result.message_ids:
            await self.deps.tasks.send(message_id, turn.chat_id)
        for generation_id in result.generation_ids:
            await self.deps.tasks.generate(generation_id)
        if turn.ctx.needs_compression:
            await self.deps.tasks.refresh_memory(turn.chat_id)
        return TurnResult(RunStatus.SENT, turn.run_id,
                          outgoing=len(result.message_ids) + len(result.generation_ids))

    async def _apply(self, session: AsyncSession, chat: Chat, turn: _Turn,
                     decision: _Decision, prepared: list[Any], result: ApplyResult,
                     run: ReplyRun) -> None:
        actx = self._action_context(turn)
        for action, ready in zip(decision.plan.messages, prepared, strict=True):
            await handler_for(action).apply(session, action, ready, actx, result)
        now = self.deps.clock.now()
        if decision.plan.canon:
            await add_canon(session, persona_id=turn.persona.id,
                            facts=[fact.fact for fact in decision.plan.canon],
                            chat_id=turn.chat_id, event_id=run.trigger_event_id)
        if decision.plan.remember:
            # «Заметки дня»: seen by the next turn at once, read by the night summary.
            chat.day_notes = with_notes(chat.day_notes, local_day(now, turn.persona.timezone),
                                        [fact.fact for fact in decision.plan.remember])
        await self._journal_blocks(session, turn, decision)
        run.status = RunStatus.SENT
        run.error = "; ".join(result.notes) or None
        run.finished_at = now

    @staticmethod
    def _record(run: ReplyRun, decision: _Decision, recall: Recollection | None) -> None:
        run.plan = decision.plan.model_dump(mode="json")
        run.guard_verdicts = list(decision.verdicts)
        run.attempts = decision.attempts
        if recall is not None:
            run.context = {**(run.context or {}), "recall": recall.request,
                           "recall_text": recall.text}

    async def _journal_blocks(self, session: AsyncSession, turn: _Turn,
                              decision: _Decision) -> None:
        for verdict in decision.verdicts:
            if not verdict.get("ok"):
                await append_event(
                    session, chat_id=turn.chat_id, kind=EventKind.GUARD_BLOCKED,
                    actor="guard", reply_run_id=turn.run_id,
                    data={"reason": verdict.get("reason", ""), "attempt": verdict["attempt"]},
                )

    async def _block(self, turn: _Turn, decision: _Decision) -> None:
        async with transaction(self.deps.sessions) as session:
            chat = await session.get(Chat, turn.chat_id, with_for_update=True)
            run = await session.get(ReplyRun, turn.run_id)
            if chat is None or run is None:
                return
            self._record(run, decision, turn.recall)
            last = decision.verdicts[-1] if decision.verdicts else {}
            reason = str(last.get("reason", ""))
            run.status = RunStatus.BLOCKED
            run.error = f"проверка не пропустила ответ дважды: {reason}"
            run.finished_at = self.deps.clock.now()
            chat.needs_operator = True
            await self._journal_blocks(session, turn, decision)
            await append_event(session, chat_id=chat.id, kind=EventKind.OPERATOR_NEEDED,
                               actor="guard", reply_run_id=run.id, data={"reason": reason})

    async def _fail(self, turn: _Turn, error: str) -> None:
        log.warning("chat %s run %s failed: %s", turn.chat_id, turn.run_id, error)
        async with transaction(self.deps.sessions) as session:
            run = await session.get(ReplyRun, turn.run_id)
            if run is None:
                return
            run.status = RunStatus.FAILED
            run.error = error[:2000]
            run.finished_at = self.deps.clock.now()
