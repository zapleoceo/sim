"""Persona voice notes: AI reply text → Google Gemini TTS (through the AIbroker) → OGG/Opus.

Rendered by the SEND job right before delivery (never at queue time: the human-like delay
and an operator take-over can still cancel the message, and a cancelled note must cost
nothing). The note is kept in the media store so the admin dialog can play what was sent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sim.adapters.audio import OGG_MIME
from sim.adapters.media import MediaStore
from sim.ports.speech import SpeechPort

VOICE_NOTE = "voice"          # Message.media_kind of an outgoing voice note
VOICE_WORKFLOW = "voice-reply"


class VoiceEncoder(Protocol):
    async def to_voice_note(self, audio: bytes) -> bytes: ...


@dataclass(frozen=True)
class VoiceNote:
    audio: bytes                  # OGG/Opus
    duration_s: float | None
    cost_usd: float | None
    media_url: str


class VoiceRenderer:
    def __init__(self, speech: SpeechPort, encoder: VoiceEncoder, store: MediaStore) -> None:
        self.speech = speech
        self.encoder = encoder
        self.store = store

    async def render(self, text: str, *, voice: str, style: str = "") -> VoiceNote:
        """Raises SpeechError (broker) or AudioError (transcoding); the caller decides the
        fallback."""
        speech = await self.speech.synthesize(text, voice=voice, style=style,
                                              workflow=VOICE_WORKFLOW)
        audio = await self.encoder.to_voice_note(speech.audio)
        name = self.store.save(audio, OGG_MIME.split("/", 1)[1])
        return VoiceNote(audio=audio, duration_s=speech.duration_s, cost_usd=speech.cost_usd,
                         media_url=f"/api/media/{name}")
