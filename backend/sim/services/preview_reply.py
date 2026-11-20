"""The shared synchronous and queued test-chat computation."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sim.adapters.audio import AudioError
from sim.adapters.db.models import Persona, Strategy
from sim.api.schemas import PreviewIn, guard_out
from sim.domain.voice import speaks
from sim.ports.speech import SpeechError
from sim.services.guard import GuardOutcome
from sim.services.persona_ambience import voice_ambience
from sim.services.preview_stages import PreviewStages
from sim.services.prompt import Turn

if TYPE_CHECKING:
    from sim.services.conversation import ConversationService
    from sim.wiring import Container


def preview_guard(g: GuardOutcome | None) -> dict[str, object] | None:
    """The draft the operator may want to see: the original of a rewrite, the blocked text."""
    if g is None:
        return None
    draft = g.original_text if g.verdict == "rewritten" else (g.text if g.verdict == "blocked" else None)
    return guard_out(g.verdict, g.violations, g.model, draft)


async def preview_voice(
    c: Container, persona: Persona, reply: str, voice_input: bool,
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


async def execute_preview(
    persona: Persona, body: PreviewIn, c: Container, service: ConversationService,
    *, text_only: bool = False, strategy: Strategy | None = None,
) -> dict:
    history = [Turn(h.role, h.text) for h in body.history]
    result = await service.preview(
        persona, body.message, history, stage=body.stage, status=body.status,
        needs=body.needs, next_step=body.next_step, strategy=strategy,
        stages=PreviewStages(body.stages.schedule, body.stages.debounce, body.stages.decision_context),
    )
    d = result.decision
    return {
        "voice": None if text_only else await preview_voice(c, persona, result.reply, body.voice_input),
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
        "stages": result.diagnostics,
        "model": result.model,
        "latency_ms": result.latency_ms,
    }
