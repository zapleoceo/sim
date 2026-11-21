"""`{"type": "voice"}`: TTS (Broker, WAV) -> OGG/Opus (ffmpeg) -> media library -> voice note.

Synthesis happens in `prepare`, before the turn's transaction, so the voice keeps its place in
the plan order. If TTS fails the same words go out as a text message (noted in the run)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.domain.enums import Author, LlmPurpose, MediaKind, MediaSource, MessageKind
from sim.domain.plan import VoiceAction, tts_style
from sim.domain.presence import Busy, busy_with
from sim.ports import PortError
from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.actions.text import enqueue_text
from sim.services.llm_log import logged_call
from sim.services.media_library import add_media
from sim.services.outbox import enqueue_message

VOICE_MIME = "audio/ogg"
VOICE_FILE_NAME = "voice.ogg"


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
        try:
            audio = await logged_call(
                deps.sessions, purpose=LlmPurpose.TTS, model=persona.model_tts,
                request={"text": action.text, "voice": persona.voice_id,
                         "style": style},
                call=lambda: deps.speech.synthesize(text=action.text, voice=persona.voice_id,
                                                    style=style,
                                                    model=persona.model_tts),
                chat_id=ctx.chat_id, reply_run_id=ctx.reply_run_id,
            )
            ogg, duration = await deps.tools.wav_to_ogg_opus(audio.data)
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
