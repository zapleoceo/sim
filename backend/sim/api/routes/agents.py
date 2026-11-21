"""«Агенты» of a persona (docs/agents.md, docs/api.md): the registry of every AI function and one
persona's settings of each — model, key tier, prompt, max_tokens — plus the guard's rule list.

Model ids are checked against the Broker catalog only when it can be read and is not empty, and
only the ids that changed: a model the catalog lists as unavailable (or with unknown
availability) still saves, because availability changes and must never block the owner."""
from __future__ import annotations

import logging
from typing import Any

from litestar import get, post, put
from litestar.exceptions import ClientException, NotFoundException
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.context import ApiContext
from sim.api.queries import persona_or_404
from sim.api.schemas import AgentIn, AgentsIn
from sim.db.models import Persona
from sim.domain.agents import (
    AGENTS,
    GROUP_TITLES,
    MAX_MAX_TOKENS,
    MIN_MAX_TOKENS,
    REGISTRY,
    AgentKey,
    AgentSpec,
    KeyTier,
    agent_config,
    clean_override,
    overrides_of,
    own_model,
    own_prompt,
)
from sim.ports import LlmPort
from sim.services.context.instructions import CALL_RULES, FORMAT_RULES

logger = logging.getLogger(__name__)

Json = dict[str, Any]

# Fixed rules the code adds to an agent's prompt; shown on its card, never edited.
READONLY_PROMPTS: dict[str, str] = {AgentKey.REPLY: FORMAT_RULES, AgentKey.LIVE: CALL_RULES}
TIER_LABELS: dict[KeyTier, str] = {
    KeyTier.AUTO: "Авто", KeyTier.PAID: "Платный", KeyTier.FREE: "Бесплатный",
}


async def unknown_models(llm: LlmPort, wanted: set[str]) -> set[str]:
    """The ids of `wanted` the Broker catalog does not list. An unreachable or empty catalog
    proves nothing, so then every id is accepted."""
    if not wanted:
        return set()
    try:
        catalog = await llm.list_models()
    except Exception:
        logger.warning("model catalog unavailable, agent models saved unchecked", exc_info=True)
        return set()
    if not catalog:
        return set()
    return wanted - {model.id for model in catalog}


def spec_view(spec: AgentSpec) -> Json:
    return {
        "key": spec.key.value, "title": spec.title, "group": spec.group.value,
        "description": spec.description, "port": spec.port.value,
        "model_filter": [match.as_json() for match in spec.model_filter],
        "key_tier": spec.key_tier.value, "model": spec.model,
        "fallback": spec.fallback.value if spec.fallback else None,
        "prompt": spec.prompt, "readonly_prompt": READONLY_PROMPTS.get(spec.key),
        "placeholders": list(spec.placeholders), "max_tokens": spec.max_tokens,
    }


def registry_view() -> Json:
    return {
        "groups": [{"key": group.value, "title": title, "description": description,
                    "agents": [spec.key.value for spec in AGENTS if spec.group == group]}
                   for group, (title, description) in GROUP_TITLES.items()],
        "agents": [spec_view(spec) for spec in AGENTS],
        "key_tiers": [{"value": tier.value, "label": label}
                      for tier, label in TIER_LABELS.items()],
        "max_tokens_range": {"min": MIN_MAX_TOKENS, "max": MAX_MAX_TOKENS},
    }


def agent_view(persona: Persona, spec: AgentSpec) -> Json:
    """One agent of the persona: the owner's choices (null / "" = by default) and what is in
    effect (`effective_model` after the default and the fallback)."""
    stored = overrides_of(persona, spec.key)
    config = agent_config(persona, spec.key)
    return {
        "model": own_model(spec, stored),
        "key_tier": config.key_tier.value,
        "prompt": own_prompt(spec, stored) or "",
        "max_tokens": config.max_tokens if spec.max_tokens else None,
        "effective_model": config.model,
    }


def agents_view(persona: Persona) -> Json:
    return {"agents": {spec.key.value: agent_view(persona, spec) for spec in AGENTS},
            "guard_rules": persona.guard_rules}


def checked(key: str, data: AgentIn) -> tuple[AgentSpec, dict[str, Any]]:
    """The spec and the stored form of one sent agent; 400 for what the agent does not have."""
    spec = REGISTRY.get(key)
    if spec is None:
        raise ClientException(detail=f"Неизвестный агент: {key}")
    if data.prompt.strip() and not spec.has_prompt:
        raise ClientException(detail=f"{spec.title}: промт не настраивается")
    if data.max_tokens is not None and not spec.max_tokens:
        raise ClientException(detail=f"{spec.title}: max_tokens не настраивается")
    return spec, clean_override(spec, {"model": data.model, "key_tier": data.key_tier,
                                       "prompt": data.prompt, "max_tokens": data.max_tokens})


@get("/agents/registry")
async def get_registry() -> Json:
    return registry_view()


@get("/personas/{persona_id:int}/agents")
async def get_agents(persona_id: int, db: AsyncSession) -> Json:
    return agents_view(await persona_or_404(db, persona_id))


@put("/personas/{persona_id:int}/agents")
async def update_agents(persona_id: int, data: AgentsIn, ctx: ApiContext,
                        db: AsyncSession) -> Json:
    persona = await persona_or_404(db, persona_id)
    current = dict(persona.agents or {})
    changed: set[str] = set()
    for key, sent in data.agents.items():
        spec, override = checked(key, sent)
        model = override.get("model")
        if model and model != own_model(spec, overrides_of(persona, key)):
            changed.add(model)
        if override:
            current[key] = override
        else:
            current.pop(key, None)
    if missing := await unknown_models(ctx.deps.llm, changed):
        raise ClientException(
            detail=f"Модели нет в каталоге Broker: {', '.join(sorted(missing))}")
    persona.agents = current
    if isinstance(data.guard_rules, str):
        persona.guard_rules = data.guard_rules
    await db.flush()
    return agents_view(persona)


@post("/personas/{persona_id:int}/agents/{key:str}/reset", status_code=200)
async def reset_agent(persona_id: int, key: str, db: AsyncSession) -> Json:
    """Back to the registry defaults: model, tier, prompt and max_tokens."""
    if key not in REGISTRY:
        raise NotFoundException(detail=f"Неизвестный агент: {key}")
    persona = await persona_or_404(db, persona_id)
    persona.agents = {k: v for k, v in (persona.agents or {}).items() if k != key}
    await db.flush()
    return agents_view(persona)


ROUTES = [get_registry, get_agents, update_agents, reset_agent]
