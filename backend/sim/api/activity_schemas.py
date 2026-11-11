"""Safe projections for activity screens; never expose raw transport diagnostics."""
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sim.adapters.db.models import Channel, Client, LlmCall, Message, Persona, TraceEvent
from sim.api.schemas import dt, message_out


def context_out(client: Client | None, persona: Persona | None, channel: Channel | None) -> dict[str, Any]:
    return {
        "client": {"id": client.id, "display_name": client.display_name, "avatar_url": client.avatar_url} if client else None,
        "persona": {"id": persona.id, "name": persona.name} if persona else None,
        "channel": {"id": channel.id, "name": channel.name, "kind": channel.kind.value,
                    "account_label": channel.account_label} if channel else None,
    }


def message_activity_out(message: Message, client: Client, persona: Persona, channel: Channel,
                         sent_at: datetime | None) -> dict[str, Any]:
    return {
        **message_out(message), **context_out(client, persona, channel),
        "dialog_id": message.dialog_id, "sent_at": dt(sent_at),
        "error": "Сообщение требует проверки оператором" if message.error else None,
    }


def llm_activity_out(call: LlmCall, client: Client | None, persona: Persona | None,
                     channel: Channel | None) -> dict[str, Any]:
    return {
        **context_out(client, persona, channel),
        "id": call.id, "created_at": dt(call.created_at), "dialog_id": call.dialog_id,
        "workflow": call.workflow, "capability": call.capability, "model": call.model,
        "provider": call.provider, "tokens_in": call.tokens_in, "tokens_out": call.tokens_out,
        "cached_tokens": call.cached_tokens, "cost_usd": call.cost_usd,
        "latency_ms": call.latency_ms, "finish_reason": call.finish_reason, "has_error": bool(call.error),
    }


def trace_step_out(event: TraceEvent, client_id: int | None, client_name: str | None) -> dict[str, Any]:
    """One step of the system log. Errors and paths were masked when written."""
    return {
        "id": event.id, "trace_id": event.trace_id, "kind": event.kind, "name": event.name,
        "status": event.status, "started_at": dt(event.started_at), "duration_ms": event.duration_ms,
        "attempt": event.attempt, "http_status": event.http_status, "actor": event.actor,
        "error": event.error, "detail": event.detail or {},
        "client": {"id": client_id, "display_name": client_name or ""} if client_id else None,
        "admin_id": event.admin_id, "dialog_id": event.dialog_id, "channel_id": event.channel_id,
        "job_id": event.job_id, "message_id": event.message_id, "llm_call_id": event.llm_call_id,
    }


STATUS_RANK = {"skipped": -1, "ok": 0, "retry": 1, "error": 2}


def trace_out(trace_id: str, rows: Sequence[Any], calls: dict[int, LlmCall]) -> dict[str, Any]:
    """A whole trace as a timeline: steps with their offset from the first one, LLM usage,
    the total span and the worst outcome. A job that failed and then succeeded is `ok`."""
    events = [row[0] for row in rows]
    start = min(e.started_at for e in events)
    end = max(e.started_at + timedelta(milliseconds=e.duration_ms or 0) for e in events)
    steps = []
    for event, client_id, client_name in rows:
        step = trace_step_out(event, client_id, client_name)
        step["offset_ms"] = int((event.started_at - start).total_seconds() * 1000)
        step["in_trace"] = event.trace_id == trace_id
        if (call := calls.get(event.llm_call_id or 0)) is not None:
            step["llm"] = {"model": call.model, "provider": call.provider, "tokens_in": call.tokens_in,
                           "tokens_out": call.tokens_out, "cached_tokens": call.cached_tokens,
                           "cost_usd": call.cost_usd, "finish_reason": call.finish_reason}
        steps.append(step)
    return {
        "trace_id": trace_id, "started_at": dt(start), "finished_at": dt(end),
        "duration_ms": int((end - start).total_seconds() * 1000), "status": trace_status(events),
        "cost_usd": round(sum(c.cost_usd or 0 for c in calls.values()), 6),
        "steps": steps,
    }


def trace_status(events: Sequence[TraceEvent]) -> str:
    """The worst final outcome. Only a job's last attempt counts, and the failed steps inside a
    job that finally succeeded (an LLM error that a retry fixed) do not either."""
    jobs: dict[int, str] = {}
    for e in events:  # oldest first: the last attempt wins
        if e.kind == "job" and e.job_id:
            jobs[e.job_id] = e.status
    outcomes = list(jobs.values()) + [
        e.status for e in events
        if not (e.kind == "job" and e.job_id) and not (e.job_id and jobs.get(e.job_id) == "ok")
    ]
    return max(outcomes, key=lambda s: STATUS_RANK.get(s, 0), default="ok")
