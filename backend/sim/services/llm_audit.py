"""Audit of every broker call (llm_call table) plus its step in the system trace log.

Rows are written in their OWN transaction: a failed call must stay in the log even when the
caller's unit of work (the job) rolls back."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.db.models import LlmCall
from sim.adapters.db.session import transaction
from sim.clock import Clock, utcnow
from sim.ports.llm import LLMError, LLMPort, LLMResult
from sim.services import tracing
from sim.services.tracing import Tracer

log = logging.getLogger(__name__)


def step_kind(workflow: str) -> str:
    """Guard calls (production "guard", simulation "sim_guard") are their own step kind."""
    return tracing.GUARD if "guard" in workflow else tracing.LLM


class AuditedLLM:
    """LLMPort wrapper used by services: every chat() leaves one llm_call row and one trace step."""

    def __init__(
        self,
        llm: LLMPort,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock = utcnow,
        tracer: Tracer | None = None,
    ) -> None:
        self.llm = llm
        self.factory = factory
        self.clock = clock
        self.tracer = tracer

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        capability: str = "chat:sales",
        model: str | None = None,
        json_mode: bool = False,
        workflow: str = "",
        dialog_id: int | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        started_at, start = self.clock(), time.perf_counter()
        try:
            result = await self.llm.chat(
                messages,
                capability=capability,
                model=model,
                json_mode=json_mode,
                max_tokens=max_tokens,
                temperature=temperature,
                workflow=workflow or None,
            )
        except LLMError as exc:
            call = LlmCall(
                dialog_id=dialog_id,
                workflow=workflow,
                capability=capability,
                model=model,
                latency_ms=tracing.elapsed_ms(start),
                error=str(exc)[:1000],
            )
            await self.record(call, started_at)
            raise
        call = LlmCall(
            dialog_id=dialog_id,
            workflow=workflow,
            capability=capability,
            model=result.model or model,
            provider=result.provider,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cached_tokens=result.cached_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            job_id=result.job_id,
            finish_reason=result.finish_reason,
        )
        await self.record(call, started_at)
        return result

    async def record(self, call: LlmCall, started_at: datetime | None = None) -> None:
        call.created_at = self.clock()
        call.trace_id = tracing.current_trace_id()
        try:
            async with transaction(self.factory) as session:
                session.add(call)
        except Exception:  # the audit must never break a conversation
            log.exception("llm_call audit write failed")
            return
        if self.tracer is not None:
            await self.tracer.record(
                step_kind(call.workflow),
                f"{call.workflow or 'llm'} · {call.capability}",
                status=tracing.ERROR if call.error else tracing.OK,
                started_at=started_at,
                duration_ms=call.latency_ms,
                error=call.error,
                detail={"model": call.model, "provider": call.provider, "tokens_in": call.tokens_in,
                        "tokens_out": call.tokens_out, "cost_usd": call.cost_usd,
                        "finish_reason": call.finish_reason},
                dialog_id=call.dialog_id,
                llm_call_id=call.id,
            )


AuditFactory = Callable[[], AuditedLLM]
