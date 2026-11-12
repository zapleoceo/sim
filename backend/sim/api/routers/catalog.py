"""Personas, strategies and the Broker model catalog."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.exc import IntegrityError

from sim.adapters.audio import AudioError
from sim.adapters.db.models import Persona, Strategy
from sim.adapters.db.repositories.catalog import PersonaRepo, StrategyRepo
from sim.api.deps import App, Authed, Db, Owner, authed, owner_only
from sim.api.schemas import (
    PersonaIn,
    PreviewIn,
    StrategyIn,
    guard_out,
    persona_out,
    strategy_out,
)
from sim.domain.voice import speaks
from sim.ports.llm import LLMError
from sim.ports.speech import SpeechError
from sim.services.conversation import ConversationError
from sim.services.guard import GuardOutcome
from sim.services.models import catalog_items
from sim.services.preview_media import MAX_BYTES, AttachmentError
from sim.services.prompt import Turn
from sim.wiring import Container

personas = APIRouter(prefix="/api/personas", tags=["personas"], dependencies=[Depends(authed)])
strategies = APIRouter(prefix="/api/strategies", tags=["strategies"], dependencies=[Depends(authed)])
models = APIRouter(prefix="/api/models", tags=["models"], dependencies=[Depends(owner_only)])


async def persona_or_404(s: Db, persona_id: int) -> Persona:
    persona = await PersonaRepo(s).get(persona_id)
    if persona is None:
        raise HTTPException(404, "Persona not found")
    return persona


async def strategy_or_404(s: Db, strategy_id: int) -> Strategy:
    strategy = await StrategyRepo(s).get(strategy_id)
    if strategy is None:
        raise HTTPException(404, "Strategy not found")
    return strategy


async def check_strategy(s: Db, strategy_id: int | None) -> None:
    if strategy_id is not None and await StrategyRepo(s).get(strategy_id) is None:
        raise HTTPException(422, "strategy_id does not exist")


# ---- personas -----------------------------------------------------------------------------
@personas.get("")
async def list_personas(s: Db):
    return {"items": [persona_out(p) for p in await PersonaRepo(s).all()]}


@personas.post("", status_code=201)
async def create_persona(body: PersonaIn, s: Db, _: Owner):
    await check_strategy(s, body.strategy_id)
    if await PersonaRepo(s).by_name(body.name):
        raise HTTPException(409, "A persona with this name already exists")
    return persona_out(await PersonaRepo(s).add(Persona(**body.model_dump())))


@personas.get("/{persona_id}")
async def get_persona(persona_id: int, s: Db):
    return persona_out(await persona_or_404(s, persona_id))


@personas.put("/{persona_id}")
async def update_persona(persona_id: int, body: PersonaIn, s: Db, _: Owner):
    persona = await persona_or_404(s, persona_id)
    await check_strategy(s, body.strategy_id)
    for key, value in body.model_dump().items():
        setattr(persona, key, value)
    try:
        await s.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "A persona with this name already exists") from exc
    return persona_out(persona)


@personas.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: int, s: Db, _: Owner) -> Response:
    persona = await persona_or_404(s, persona_id)
    try:
        await PersonaRepo(s).delete(persona)
    except IntegrityError as exc:
        raise HTTPException(409, "Persona is used by channels or clients") from exc
    return Response(status_code=204)


def preview_guard(g: GuardOutcome | None) -> dict[str, object] | None:
    """The draft the operator may want to see: the original of a rewrite, the blocked text."""
    if g is None:
        return None
    draft = g.original_text if g.verdict == "rewritten" else (g.text if g.verdict == "blocked" else None)
    return guard_out(g.verdict, g.violations, g.model, draft)


@personas.post("/{persona_id}/preview/attachment")
async def preview_attachment(
    persona_id: int, c: App, s: Db, _: Authed,
    file: Annotated[UploadFile, File()],
    voice: Annotated[bool, Form()] = False,
):
    """A voice note recorded in the admin or an attached file → text the persona reads."""
    await persona_or_404(s, persona_id)
    data = await file.read(MAX_BYTES + 1)
    try:
        att = await c.preview_attachments().read(
            data, file.filename or "file", file.content_type or "", voice=voice
        )
    except AttachmentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"kind": att.kind, "name": att.name, "text": att.text, "media_url": att.media_url}


async def preview_voice(
    c: Container, persona: Persona, reply: str, voice_input: bool
) -> dict[str, object] | None:
    """The persona's voice note for this reply, by the same rule as a channel (`speaks`)."""
    renderer = c.voice_renderer()
    last_kind = "voice" if voice_input else None
    if not reply or renderer is None or not speaks(persona.voice_mode, persona.voice_id, last_kind):
        return None
    try:
        note = await renderer.render(reply, voice=persona.voice_id, style=persona.voice_style)
    except (SpeechError, AudioError) as exc:
        return {"media_url": None, "duration_s": None, "error": str(exc)[:300]}
    return {"media_url": note.media_url, "duration_s": note.duration_s, "error": None}


@personas.post("/{persona_id}/preview")
async def preview_persona(persona_id: int, body: PreviewIn, c: App, s: Db, _: Authed):
    persona = await persona_or_404(s, persona_id)
    history = [Turn(h.role, h.text) for h in body.history]
    try:
        result = await c.conversation(s).preview(persona, body.message, history)
    except ConversationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"Broker error: {exc}") from exc
    d = result.decision
    return {
        "voice": await preview_voice(c, persona, result.reply, body.voice_input),
        "reply": result.reply,
        "decision": None
        if d is None
        else {
            "reply": d.reply, "stage": d.stage.value if d.stage else None, "needs": d.needs,
            "next_step": d.next_step, "handoff_to_operator": d.handoff_to_operator,
            "refused": d.refused, "stop_contact": d.stop_contact,
        },
        "guard": preview_guard(result.guard),
        "model": result.model,
        "latency_ms": result.latency_ms,
    }


# ---- strategies ---------------------------------------------------------------------------
@strategies.get("")
async def list_strategies(s: Db):
    return {"items": [strategy_out(x) for x in await StrategyRepo(s).all()]}


@strategies.post("", status_code=201)
async def create_strategy(body: StrategyIn, s: Db, _: Owner):
    if await StrategyRepo(s).by_name(body.name):
        raise HTTPException(409, "A strategy with this name already exists")
    return strategy_out(await StrategyRepo(s).add(Strategy(**body.model_dump())))


@strategies.get("/{strategy_id}")
async def get_strategy(strategy_id: int, s: Db):
    return strategy_out(await strategy_or_404(s, strategy_id))


@strategies.put("/{strategy_id}")
async def update_strategy(strategy_id: int, body: StrategyIn, s: Db, _: Owner):
    strategy = await strategy_or_404(s, strategy_id)
    # Only the fields the client sent: an older client that does not know `prompt` / `notes`
    # must not wipe them.
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(strategy, key, value)
    try:
        await s.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "A strategy with this name already exists") from exc
    return strategy_out(strategy)


@strategies.delete("/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: int, s: Db, _: Owner) -> Response:
    await StrategyRepo(s).delete(await strategy_or_404(s, strategy_id))
    return Response(status_code=204)


# ---- models -------------------------------------------------------------------------------
@models.get("")
async def list_models(c: App):
    try:
        rows = await c.llm.list_models()
    except LLMError as exc:
        raise HTTPException(502, f"Broker error: {exc}") from exc
    return {"items": catalog_items(rows)}
