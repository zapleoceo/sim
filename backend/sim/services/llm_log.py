"""One helper for every Broker call: run it, time it and store an `llm_call` row (request with
the agent's key tier, response, usage, error) in its own short transaction, so the journal
survives a failed turn.

Bytes never go into the log: inline files are replaced by their mime and size."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.db.models import LlmCall
from sim.db.session import transaction
from sim.domain.enums import LlmPurpose
from sim.ports import ChatMessage, Usage


class _HasUsage(Protocol):
    @property
    def usage(self) -> Usage: ...


def describe_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Chat messages as JSON for the log: texts as is, inline files as {mime, size_bytes}."""
    out = []
    for message in messages:
        parts: list[dict[str, Any]] = []
        for part in message.parts:
            if part.data is not None:
                parts.append({"mime": part.mime, "size_bytes": len(part.data)})
            else:
                parts.append({"text": part.text or ""})
        out.append({"role": message.role, "parts": parts})
    return out


def describe_result(result: Any) -> dict[str, Any]:
    """The useful part of a port result for the log (text, or mime/size of produced bytes)."""
    response: dict[str, Any] = {}
    text = getattr(result, "text", None)
    if isinstance(text, str):
        response["text"] = text
    data = getattr(result, "data", None)
    if isinstance(data, bytes | bytearray):
        response["mime"] = getattr(result, "mime", None)
        response["size_bytes"] = len(data)
    duration = getattr(result, "duration_s", None)
    if duration is not None:
        response["duration_s"] = duration
    usage = getattr(result, "usage", None)
    if isinstance(usage, Usage) and usage.job_id:
        response["job_id"] = usage.job_id
    return response


async def logged_call[R: _HasUsage](
    sessions: async_sessionmaker[AsyncSession],
    *,
    purpose: LlmPurpose,
    model: str,
    request: dict[str, Any],
    call: Callable[[], Awaitable[R]],
    chat_id: int | None = None,
    reply_run_id: int | None = None,
    key_tier: str | None = None,
) -> R:
    """Run `call`, log it, return its result. A failed call is logged with the error and the
    exception is re-raised unchanged. `key_tier` (the agent's, docs/agents.md) is logged in
    the request."""
    if key_tier is not None:
        request = {**request, "key_tier": str(key_tier)}
    started = time.perf_counter()
    try:
        result = await call()
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        await _store(sessions, LlmCall(
            purpose=purpose, model=model, request=request, response={}, latency_ms=elapsed,
            error=str(exc)[:2000] or type(exc).__name__, chat_id=chat_id,
            reply_run_id=reply_run_id,
        ))
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    usage = result.usage
    await _store(sessions, LlmCall(
        purpose=purpose, model=usage.model or model, request=request,
        response=describe_result(result), tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out, cost_usd=usage.cost_usd,
        latency_ms=usage.latency_ms if usage.latency_ms is not None else elapsed,
        chat_id=chat_id, reply_run_id=reply_run_id,
    ))
    return result


async def _store(sessions: async_sessionmaker[AsyncSession], row: LlmCall) -> None:
    async with transaction(sessions) as session:
        session.add(row)
