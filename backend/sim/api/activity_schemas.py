"""Safe projections for activity screens; never expose raw transport diagnostics."""
from datetime import datetime
from typing import Any

from sim.adapters.db.models import Channel, Client, LlmCall, Message, Persona
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
