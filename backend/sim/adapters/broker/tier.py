"""The optional `key_tier` request field of the Broker (docs/broker.md, docs/agents.md): "paid"
or "free" makes the Broker use only keys of that tier for the call. "auto" is never sent, so
an older Broker that does not know the field gets exactly the request it always got.
Subscription models (claude-sub/, codex-sub/) run on the owner's subscription and ignore the
tier, so it is not sent for them either."""
from __future__ import annotations

from typing import Any

from sim.adapters.broker.http import is_subscription_model
from sim.ports import AUTO_TIER

KEY_TIER_FIELD = "key_tier"


def sent_tier(key_tier: str, model: str = "") -> str | None:
    """The tier to send, or None when the field is left out."""
    tier = (key_tier or AUTO_TIER).strip().lower()
    if tier == AUTO_TIER or is_subscription_model(model):
        return None
    return tier


def with_tier(body: dict[str, Any], key_tier: str, model: str = "") -> dict[str, Any]:
    """`body` with `key_tier` added when it is to be sent (changed in place and returned)."""
    tier = sent_tier(key_tier, model)
    if tier is not None:
        body[KEY_TIER_FIELD] = tier
    return body
