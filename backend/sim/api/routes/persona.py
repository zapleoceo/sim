"""`/api/persona`, `/api/models`, `/api/voices`, `/api/instructions`, `/api/channels`: the
persona page. Model ids are checked against the Broker catalog only when it can be read and is
not empty; a model the catalog lists as unavailable (or with unknown availability) still saves,
because availability changes and must never block the owner from saving the page."""
from __future__ import annotations

import logging
from typing import Any

from litestar import get, put
from litestar.exceptions import ClientException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.queries import current_persona
from sim.api.schemas import PERSONA_MODEL_FIELDS, PersonaPatchIn, check_timezone, sent_fields
from sim.db.models import Channel, MediaAsset
from sim.ports import LlmPort
from sim.services.context.instructions import FORMAT_RULES
from sim.services.voices import GEMINI_VOICES

logger = logging.getLogger(__name__)

Json = dict[str, Any]


async def unknown_models(llm: LlmPort, wanted: set[str]) -> set[str]:
    """The ids of `wanted` the Broker catalog does not list. An unreachable or empty catalog
    proves nothing, so then every id is accepted."""
    if not wanted:
        return set()
    try:
        catalog = await llm.list_models()
    except Exception:
        logger.warning("model catalog unavailable, persona models saved unchecked",
                       exc_info=True)
        return set()
    if not catalog:
        return set()
    return wanted - {model.id for model in catalog}


@get("/persona")
async def get_persona(db: AsyncSession) -> Json:
    return views.persona_view(await current_persona(db))


@put("/persona")
async def update_persona(data: PersonaPatchIn, ctx: ApiContext, db: AsyncSession) -> Json:
    persona = await current_persona(db)
    changes = {f: v for f, v in sent_fields(data).items() if getattr(persona, f) != v}
    if "timezone" in changes:
        check_timezone(changes["timezone"])
    if changes.get("avatar_media_id") is not None and (
            await db.get(MediaAsset, changes["avatar_media_id"]) is None):
        raise ClientException(detail="Аватар: файл не найден в медиатеке")
    # Only changed ids are checked: a model that left the catalog must not block other edits.
    wanted = {changes[f] for f in PERSONA_MODEL_FIELDS if changes.get(f)}
    if missing := await unknown_models(ctx.deps.llm, wanted):
        raise ClientException(
            detail=f"Модели нет в каталоге Broker: {', '.join(sorted(missing))}")
    for field, value in changes.items():
        setattr(persona, field, value)
    await db.flush()
    return views.persona_view(persona)


@get("/models")
async def list_models(ctx: ApiContext) -> Json:
    models = await ctx.deps.llm.list_models()
    return {"items": [
        {"id": m.id, "available": m.available, "input_modalities": list(m.input_modalities),
         "output_modalities": list(m.output_modalities), "capabilities": list(m.capabilities)}
        for m in models
    ]}


@get("/voices")
async def list_voices() -> Json:
    return {"items": [{"id": v.id, "label": v.label} for v in GEMINI_VOICES]}


@get("/instructions")
async def get_instructions() -> Json:
    return {"text": FORMAT_RULES}


@get("/channels")
async def list_channels(db: AsyncSession) -> Json:
    channels = await db.scalars(select(Channel).order_by(Channel.id))
    return {"items": [views.channel_view(c) for c in channels]}


ROUTES = [get_persona, update_persona, list_models, list_voices, get_instructions,
          list_channels]
