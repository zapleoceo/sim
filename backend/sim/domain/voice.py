"""Persona voice vocabulary: Google Gemini TTS prebuilt voices (served through the AIbroker).

The list mirrors the Broker's accepted voices (`POST /v1/speech`); the admin form offers the
same names (frontend `persona-voices.ts`)."""
from __future__ import annotations

from sim.domain.enums import VoiceMode

# Voices the owner tested and chose (2026-11-13): Kore, and Vindemiatrix as Marina's homely voice,
# picked from a casting of one text read "like to a friend in the kitchen". The other female voices
# were removed from the admin on the owner's request; the Broker itself accepts all 30 Gemini voices.
GEMINI_VOICES: tuple[str, ...] = ("Kore", "Vindemiatrix")
VOICE_KINDS = ("audio", "voice")


def speaks(voice_mode: str, voice_id: str, last_inbound_kind: str | None) -> bool:
    """Whether the next AI message of a persona goes out as a voice note."""
    if voice_id not in GEMINI_VOICES:
        return False
    if voice_mode == VoiceMode.ALWAYS:
        return True
    return voice_mode == VoiceMode.MIRROR and last_inbound_kind in VOICE_KINDS
