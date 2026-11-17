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
    prompt_version_out,
    strategy_out,
)
from sim.domain.voice import speaks
from sim.ports.llm import LLMError
from sim.ports.speech import SpeechError
from sim.services.guard import GuardOutcome
from sim.services.journal import CommunicationJournal, admin_who
from sim.services.models import catalog_items
from sim.services.persona_ambience import voice_ambience
from sim.services.preview_media import MAX_BYTES
from sim.services.prompt import Turn
from sim.services.prompt_versions import PromptVersions, VersionNotFound, restored_note
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
async def check_model_routes(c: App, body: PersonaIn, previous: Persona | None = None) -> None:
    changed = {key: getattr(body, key) for key in ("identity_model", "fallback_model")
               if getattr(body, key) and (previous is None or getattr(previous, key) != getattr(body, key))}
    if not changed:
        return
    try:
        rows = {row.get("id"): row for row in await c.llm.list_models()}
    except LLMError as exc:
        raise HTTPException(422, "Каталог моделей недоступен; новый маршрут не сохранён") from exc
    for key, value in changed.items():
        row = rows.get(value, {})
        if row.get("available") is not True:
            raise HTTPException(422, "Выберите доступную модель проекта")
        if key == "fallback_model" and row.get("billing_type") != "subscription":
            raise HTTPException(422, "Резерв должен быть подписочной моделью проекта")


@personas.get("")
async def list_personas(s: Db):
    return {"items": [persona_out(p) for p in await PersonaRepo(s).all()]}


@personas.post("", status_code=201)
async def create_persona(body: PersonaIn, c: App, s: Db, p: Owner):
    await check_strategy(s, body.strategy_id)
    await check_model_routes(c, body)
    if await PersonaRepo(s).by_name(body.name):
        raise HTTPException(409, "A persona with this name already exists")
    persona = await PersonaRepo(s).add(Persona(**body.model_dump()))
    # Her first prompt is the first version of its history.
    await PromptVersions(s, clock=c.clock).save(persona, body.agent_prompt, author=admin_who(p.user).name)
    return persona_out(persona)


@personas.get("/{persona_id}")
async def get_persona(persona_id: int, s: Db):
    return persona_out(await persona_or_404(s, persona_id))


@personas.put("/{persona_id}")
async def update_persona(persona_id: int, body: PersonaIn, c: App, s: Db, p: Owner):
    persona = await persona_or_404(s, persona_id)
    await check_strategy(s, body.strategy_id)
    await check_model_routes(c, body, persona)
    after = body.model_dump()
    before = {key: getattr(persona, key) for key in after}
    prompt = after["agent_prompt"]
    for key, value in after.items():
        if key != "agent_prompt":
            setattr(persona, key, value)
    try:
        # The prompt changes only through its version history (one more version when it changed).
        await PromptVersions(s, clock=c.clock).save(persona, prompt, author=admin_who(p.user).name)
        await s.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "A persona with this name already exists") from exc
    # Another strategy, model or persona text changes how her dialogs are led: journal lines.
    names = {row.id: row.name for row in await StrategyRepo(s).all()}
    await CommunicationJournal(s, clock=c.clock).persona_edited(
        persona.id, before, after, admin_who(p.user), names)
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


async def save_prompt(c: Container, s: Db, p: Owner, persona: Persona, text: str,
                      note: str = "") -> None:
    """A restored prompt: a new version and a journal line in her dialogs."""
    before = {"agent_prompt": persona.agent_prompt}
    who = admin_who(p.user)
    if await PromptVersions(s, clock=c.clock).save(persona, text, author=who.name, note=note):
        await CommunicationJournal(s, clock=c.clock).persona_edited(
            persona.id, before, {"agent_prompt": text}, who, {})


@personas.get("/{persona_id}/prompt/versions")
async def prompt_versions(persona_id: int, c: App, s: Db):
    """Every saved text of her prompt, newest (the one in use) first."""
    await persona_or_404(s, persona_id)
    return {"items": [prompt_version_out(v) for v in await PromptVersions(s, clock=c.clock).history(persona_id)]}


@personas.post("/{persona_id}/prompt/versions/{version_id}/restore")
async def restore_prompt_version(persona_id: int, version_id: int, c: App, s: Db, p: Owner):
    """Put an older text back in use; it is saved again as the newest version."""
    persona = await persona_or_404(s, persona_id)
    versions = PromptVersions(s, clock=c.clock)
    try:
        old = await versions.get(persona_id, version_id)
    except VersionNotFound as exc:
        raise HTTPException(404, "Prompt version not found") from exc
    await save_prompt(c, s, p, persona, old.text, restored_note(old))
    return {"mode": "unified", "agent_prompt": persona.agent_prompt,
            "items": [prompt_version_out(v) for v in await versions.history(persona_id)]}


@personas.post("/{persona_id}/preview/attachment")
async def preview_attachment(
    persona_id: int, c: App, s: Db, _: Authed,
    file: Annotated[UploadFile, File()],
    voice: Annotated[bool, Form()] = False,
):
    """A voice note recorded in the admin or an attached file → text the persona reads."""
    await persona_or_404(s, persona_id)
    data = await file.read(MAX_BYTES + 1)
    att = await c.preview_attachments().read(
        data, file.filename or "file", file.content_type or "", voice=voice
    )
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
        note = await renderer.render(
            reply, voice=persona.voice_id, style=persona.voice_style,
            ambience=voice_ambience(persona.voice_ambience, persona.routine,
                                    persona.timezone, c.clock()),
        )
    except (SpeechError, AudioError) as exc:
        return {"media_url": None, "duration_s": None, "error": str(exc)[:300]}
    return {"media_url": note.media_url, "duration_s": note.duration_s, "error": None}


@personas.post("/{persona_id}/preview")
async def preview_persona(persona_id: int, body: PreviewIn, c: App, s: Db, _: Authed):
    persona = await persona_or_404(s, persona_id)
    history = [Turn(h.role, h.text) for h in body.history]
    result = await c.conversation(s).preview(
        persona, body.message, history, stage=body.stage, status=body.status,
        needs=body.needs, next_step=body.next_step,
    )
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
        "context": result.context,
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
async def update_strategy(strategy_id: int, body: StrategyIn, c: App, s: Db, p: Owner):
    strategy = await strategy_or_404(s, strategy_id)
    # Only the fields the client sent: an older client that does not know `prompt` / `notes`
    # must not wipe them.
    after = body.model_dump(exclude_unset=True)
    before = {key: getattr(strategy, key) for key in after}
    for key, value in after.items():
        setattr(strategy, key, value)
    try:
        await s.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "A strategy with this name already exists") from exc
    await CommunicationJournal(s, clock=c.clock).strategy_edited(
        strategy.id, before, after, admin_who(p.user))
    return strategy_out(strategy)


@strategies.delete("/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: int, s: Db, _: Owner) -> Response:
    await StrategyRepo(s).delete(await strategy_or_404(s, strategy_id))
    return Response(status_code=204)


# ---- models -------------------------------------------------------------------------------
@models.get("")
async def list_models(c: App):
    return {"items": catalog_items(await c.llm.list_models())}
