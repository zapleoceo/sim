"""Persona voice vocabulary: Google Gemini TTS prebuilt voices (served through the AIbroker).

The list mirrors the Broker's accepted voices (`POST /v1/speech`); the admin form offers the
same names (frontend `persona-voices.ts`)."""
from __future__ import annotations

from sim.domain.enums import VoiceMode

# Female voices only (Google's gender table), checked 2026-11-12 on Russian and Ukrainian:
# each one said the same phrase in both languages and Gemini ASR transcribed it back verbatim.
# Laomedeia is left out (it turned «Марина» into «Уарина»); male voices are not offered
# because the personas are women. The Broker itself accepts all 30 Gemini voices.
GEMINI_VOICES: tuple[str, ...] = (
    "Kore", "Aoede", "Leda", "Zephyr", "Callirrhoe", "Autonoe", "Despina", "Erinome",
    "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat",
)
VOICE_KINDS = ("audio", "voice")


def speaks(voice_mode: str, voice_id: str, last_inbound_kind: str | None) -> bool:
    """Whether the next AI message of a persona goes out as a voice note."""
    if voice_id not in GEMINI_VOICES:
        return False
    if voice_mode == VoiceMode.ALWAYS:
        return True
    return voice_mode == VoiceMode.MIRROR and last_inbound_kind in VOICE_KINDS
