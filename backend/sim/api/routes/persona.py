"""`/api/personas*`: the list of personas, one persona's settings, archive, «Клонировать»,
«Описать по референсам», and the shared catalogs `/api/models`, `/api/voices`,
`/api/instructions` (docs/api.md, docs/personas.md). Her life: routes/life.py, her channels:
routes/channels.py, her media library: routes/media.py.

Model ids are checked against the Broker catalog only when it can be read and is not empty; a
model the catalog lists as unavailable (or with unknown availability) still saves, because
availability changes and must never block the owner from saving the page."""
from __future__ import annotations

import logging
from typing import Any

from litestar import get, patch, post, put
from litestar.exceptions import ClientException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.queries import persona_or_404
from sim.api.schemas import (
    PERSONA_MODEL_FIELDS,
    CloneIn,
    PersonaPatchIn,
    PersonaStateIn,
    check_timezone,
    sent_fields,
)
from sim.db.models import Channel, Client, MediaAsset, Persona
from sim.ports import LlmPort
from sim.services.appearance import describe_appearance
from sim.services.context.instructions import FORMAT_RULES
from sim.services.generation import reference_ids
from sim.services.persona_clone import PersonaNameTaken, clone_persona
from sim.services.voices import GEMINI_VOICES

logger = logging.getLogger(__name__)

Json = dict[str, Any]
NAME_TAKEN = "Персонаж с таким именем уже есть"


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


@get("/personas")
async def list_personas(db: AsyncSession) -> Json:
    """Active personas first, then archived; each with its leads count and channels."""
    personas = list(await db.scalars(
        select(Persona).order_by(Persona.is_active.desc(), Persona.id)))
    leads = dict((await db.execute(
        select(Client.persona_id, func.count(Client.id)).group_by(Client.persona_id))).all())
    channels: dict[int, list[Channel]] = {}
    for channel in await db.scalars(select(Channel).order_by(Channel.id)):
        channels.setdefault(channel.persona_id, []).append(channel)
    return {"items": [views.persona_row(p, leads=leads.get(p.id, 0),
                                        channels=channels.get(p.id, []))
                      for p in personas]}


@get("/personas/{persona_id:int}")
async def get_persona(persona_id: int, db: AsyncSession) -> Json:
    return views.persona_view(await persona_or_404(db, persona_id))


@put("/personas/{persona_id:int}")
async def update_persona(persona_id: int, data: PersonaPatchIn, ctx: ApiContext,
                         db: AsyncSession) -> Json:
    persona = await persona_or_404(db, persona_id)
    changes = {f: v for f, v in sent_fields(data).items() if getattr(persona, f) != v}
    if "timezone" in changes:
        check_timezone(changes["timezone"])
    if "name" in changes:
        changes["name"] = " ".join(changes["name"].split())
        taken = await db.scalar(select(Persona.id).where(Persona.name == changes["name"],
                                                         Persona.id != persona.id))
        if not changes["name"] or taken is not None:
            raise ClientException(detail=NAME_TAKEN if taken else "Имя не может быть пустым")
    if changes.get("avatar_media_id") is not None:
        avatar = await db.get(MediaAsset, changes["avatar_media_id"])
        if avatar is None or avatar.persona_id != persona.id:
            raise ClientException(detail="Аватар: файл не найден в медиатеке персонажа")
    # Only changed ids are checked: a model that left the catalog must not block other edits.
    wanted = {changes[f] for f in PERSONA_MODEL_FIELDS if changes.get(f)}
    if missing := await unknown_models(ctx.deps.llm, wanted):
        raise ClientException(
            detail=f"Модели нет в каталоге Broker: {', '.join(sorted(missing))}")
    for field, value in changes.items():
        setattr(persona, field, value)
    await db.flush()
    return views.persona_view(persona)


@patch("/personas/{persona_id:int}")
async def set_persona_state(persona_id: int, data: PersonaStateIn, db: AsyncSession) -> Json:
    """Archive instead of delete: an archived persona answers nobody, plans no days and its
    Telegram accounts go offline at the telegram process's next refresh (≤ 60 s)."""
    persona = await persona_or_404(db, persona_id)
    persona.is_active = data.is_active
    await db.flush()
    return views.persona_view(persona)


@post("/personas/{persona_id:int}/clone", status_code=201)
async def clone(persona_id: int, data: CloneIn, ctx: ApiContext, db: AsyncSession) -> Json:
    await persona_or_404(db, persona_id)
    name = " ".join(data.name.split())
    if not name:
        raise ClientException(detail="Укажите имя нового персонажа")
    try:
        persona = await clone_persona(db, ctx.deps.storage, persona_id, name, data.parts)
    except PersonaNameTaken as exc:
        raise ClientException(detail=NAME_TAKEN) from exc
    return views.persona_view(persona)


@post("/personas/{persona_id:int}/appearance/describe", status_code=200)
async def describe_persona_appearance(persona_id: int, ctx: ApiContext,
                                      db: AsyncSession) -> Json:
    """The vision model describes her by the reference photos; nothing is saved — the owner
    edits the text and saves it with the form (`appearance`)."""
    persona = await persona_or_404(db, persona_id)
    if not persona.model_vision:
        raise ClientException(detail="Не выбрана модель распознавания")
    ids = await reference_ids(db, persona.id)
    if not ids:
        raise ClientException(detail="В медиатеке нет фото с флажком «референс внешности»")
    model = persona.model_vision
    references = []
    for media_id in ids:
        asset = await db.get(MediaAsset, media_id)
        if asset is not None:
            data = await ctx.deps.storage.get(asset.storage_key)
            references.append(await ctx.deps.tools.image_for_model(data, asset.mime))
    # The model call is logged in its own transaction (llm_call): release this one first.
    await db.commit()
    text = await describe_appearance(ctx.deps, model=model, references=references)
    return {"text": text, "references": len(references)}


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


ROUTES = [list_personas, get_persona, update_persona, set_persona_state, clone,
          describe_persona_appearance, list_models, list_voices, get_instructions]
