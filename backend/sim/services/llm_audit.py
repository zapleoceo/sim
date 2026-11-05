"""Audit of every broker call (llm_call table).

Rows are written in their OWN transaction: a failed call must stay in the log even when the
caller's unit of work (the job) rolls back."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.db.models import LlmCall
from sim.adapters.db.session import transaction
from sim.clock import Clock, utcnow
from sim.ports.llm import LLMError, LLMPort, LLMResult

log = logging.getLogger(__name__)


class AuditedLLM:
    """LLMPort wrapper used by services: every chat() leaves one llm_call row."""

    def __init__(
        self,
        llm: LLMPort,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock = utcnow,
    ) -> None:
        self.llm = llm
        self.factory = factory
        self.clock = clock

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
        start = time.perf_counter()
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
            await self.record(
                LlmCall(
                    dialog_id=dialog_id,
                    workflow=workflow,
                    capability=capability,
                    model=model,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    error=str(exc)[:1000],
                )
            )
            raise
        await self.record(
            LlmCall(
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
        )
        return result

    async def record(self, call: LlmCall) -> None:
        call.created_at = self.clock()
        try:
            async with transaction(self.factory) as session:
                session.add(call)
        except Exception:  # the audit must never break a conversation
            log.exception("llm_call audit write failed")


AuditFactory = Callable[[], AuditedLLM]
