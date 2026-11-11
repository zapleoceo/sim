"""Persona voice vocabulary: Google Gemini TTS prebuilt voices (served through the AIbroker).

The list mirrors the Broker's accepted voices (`POST /v1/speech`); the admin form offers the
same names (frontend `persona-voices.ts`)."""
from __future__ import annotations

from sim.domain.enums import VoiceMode

# Female-sounding voices first (the current personas are women), then the rest; Google
# describes each one by character, e.g. Kore "firm", Aoede "breezy", Sulafat "warm".
GEMINI_VOICES: tuple[str, ...] = (
    "Kore", "Aoede", "Leda", "Zephyr", "Callirrhoe", "Autonoe", "Despina", "Erinome",
    "Laomedeia", "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat",
    "Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Algenib", "Rasalgethi", "Alnilam", "Schedar", "Achird", "Zubenelgenubi",
    "Sadachbia", "Sadaltager",
)
VOICE_KINDS = ("audio", "voice")


def speaks(voice_mode: str, voice_id: str, last_inbound_kind: str | None) -> bool:
    """Whether the next AI message of a persona goes out as a voice note."""
    if voice_id not in GEMINI_VOICES:
        return False
    if voice_mode == VoiceMode.ALWAYS:
        return True
    return voice_mode == VoiceMode.MIRROR and last_inbound_kind in VOICE_KINDS
