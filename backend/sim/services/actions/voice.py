"""`{"type": "voice"}`: TTS (Broker, WAV) -> OGG/Opus (ffmpeg) -> media library -> voice note.
The recording gets the background sound of her current scene (`tools.voice_note`), unless the
owner turned it off (`persona.voice_ambience_enabled`) or she has no life to take a scene from.

Synthesis happens in `prepare`, before the turn's transaction, so the voice keeps its place in
the plan order. If TTS fails the same words go out as a text message (noted in the run)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona
from sim.db.session import transaction
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import Author, LlmPurpose, MediaKind, MediaSource, MessageKind
from sim.domain.plan import VoiceAction, tts_style
from sim.domain.presence import Busy, busy_with
from sim.ports import PortError
from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.actions.text import enqueue_text
from sim.services.deps import Deps
from sim.services.life.busy import stable_seed
from sim.services.life.scene import voice_ambience
from sim.services.life.store import load_life
from sim.services.llm_log import logged_call
from sim.services.media_library import add_media
from sim.services.outbox import enqueue_message

VOICE_MIME = "audio/ogg"
VOICE_FILE_NAME = "voice.ogg"


async def voice_background(deps: Deps, persona: Persona) -> tuple[str, int] | None:
    """(ambience, seed) of the scene she is in now, or None for a clean recording: the owner
    turned the background off or she has no life to take a scene from. The seed comes from the
    event, so every voice note of one event has the same background (docs/persona-life.md)."""
    if not persona.voice_ambience_enabled:
        return None
    async with transaction(deps.sessions) as session:
        life = await load_life(session, persona, deps.clock.now())
    if not life.has_life:
        return None
    scene = life.scene()
    ambience = voice_ambience(scene, life.local, enabled=True)
    if ambience is None:
        return None
    return ambience.value, stable_seed(f"{scene.day.isoformat()}:{scene.event_id}")


@dataclass(frozen=True)
class Spoken:
    data: bytes
    duration_s: float


@dataclass(frozen=True)
class VoiceFailed:
    error: str


class VoiceHandler(ActionHandler):
    action_type = VoiceAction

    def busy(self, action: Any) -> Busy | None:
        return busy_with(MessageKind.VOICE)

    async def prepare(self, action: Any, ctx: ActionContext) -> Spoken | VoiceFailed:
        deps, persona = ctx.deps, ctx.persona
        style = tts_style(persona.voice_style, action.style)
        tts = agent_config(persona, AgentKey.TTS)
        try:
            audio = await logged_call(
                deps.sessions, purpose=LlmPurpose.TTS, model=tts.model,
                request={"text": action.text, "voice": persona.voice_id,
                         "style": style},
                call=lambda: deps.speech.synthesize(text=action.text, voice=persona.voice_id,
                                                    style=style, model=tts.model,
                                                    key_tier=tts.key_tier),
                chat_id=ctx.chat_id, reply_run_id=ctx.reply_run_id, key_tier=tts.key_tier,
            )
            background = await voice_background(deps, persona)
            if background is None:
                ogg, duration = await deps.tools.wav_to_ogg_opus(audio.data)
            else:
                ogg, duration = await deps.tools.voice_note(audio.data, *background)
        except PortError as exc:
            return VoiceFailed(str(exc) or type(exc).__name__)
        return Spoken(data=ogg, duration_s=duration)

    async def apply(self, session: AsyncSession, action: Any, prepared: Any,
                    ctx: ActionContext, result: ApplyResult) -> None:
        if not isinstance(prepared, Spoken):
            error = prepared.error if isinstance(prepared, VoiceFailed) else "not prepared"
            result.notes.append(f"голосовое не получилось ({error}), отправлено текстом")
            await enqueue_text(session, ctx, result, action.text)
            return
        deps = ctx.deps
        media = await add_media(
            session, deps.storage, deps.tools, persona_id=ctx.persona.id, data=prepared.data,
            mime=VOICE_MIME, file_name=VOICE_FILE_NAME, source=MediaSource.GENERATED,
            kind=MediaKind.AUDIO, description=action.text,
        )
        if media.duration_s is None:
            media.duration_s = prepared.duration_s
        message = await enqueue_message(session, chat_id=ctx.chat_id, author=Author.PERSONA,
                                        kind=MessageKind.VOICE, media_id=media.id,
                                        reply_run_id=ctx.reply_run_id)
        result.message_ids.append(message.id)
