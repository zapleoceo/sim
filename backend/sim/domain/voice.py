"""Persona voice vocabulary: Google Gemini TTS prebuilt voices (served through the AIbroker).

The list mirrors the Broker's accepted voices (`POST /v1/speech`); the admin form offers the
same names (frontend `persona-voices.ts`)."""
from __future__ import annotations

from sim.domain.enums import VoiceMode

# The one voice the owner tested and chose (Marina's voice notes, 2026-11-12/13, with and without
# the phone-recording ambience). The other 12 female voices checked for ru/uk that day were removed
# from the admin on the owner's request; the Broker itself accepts all 30 Gemini voices.
GEMINI_VOICES: tuple[str, ...] = ("Kore",)
VOICE_KINDS = ("audio", "voice")


def speaks(voice_mode: str, voice_id: str, last_inbound_kind: str | None) -> bool:
    """Whether the next AI message of a persona goes out as a voice note."""
    if voice_id not in GEMINI_VOICES:
        return False
    if voice_mode == VoiceMode.ALWAYS:
        return True
    return voice_mode == VoiceMode.MIRROR and last_inbound_kind in VOICE_KINDS
