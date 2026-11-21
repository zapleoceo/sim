"""Gemini TTS prebuilt voices accepted by the Broker (`POST /v1/speech`). The persona page
offers this list (`GET /api/voices`); `persona.voice_id` must be one of the ids."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    id: str
    label: str


GEMINI_VOICES: tuple[Voice, ...] = (
    Voice("Zephyr", "Zephyr — яркий, женский"),
    Voice("Puck", "Puck — бодрый, мужской"),
    Voice("Charon", "Charon — информативный, мужской"),
    Voice("Kore", "Kore — уверенный, женский"),
    Voice("Fenrir", "Fenrir — живой, мужской"),
    Voice("Leda", "Leda — юный, женский"),
    Voice("Orus", "Orus — твёрдый, мужской"),
    Voice("Aoede", "Aoede — лёгкий, женский"),
    Voice("Callirrhoe", "Callirrhoe — спокойный, женский"),
    Voice("Autonoe", "Autonoe — яркий, женский"),
    Voice("Enceladus", "Enceladus — с придыханием, мужской"),
    Voice("Iapetus", "Iapetus — чёткий, мужской"),
    Voice("Umbriel", "Umbriel — спокойный, мужской"),
    Voice("Algieba", "Algieba — мягкий, мужской"),
    Voice("Despina", "Despina — мягкий, женский"),
    Voice("Erinome", "Erinome — чёткий, женский"),
    Voice("Algenib", "Algenib — с хрипотцой, мужской"),
    Voice("Rasalgethi", "Rasalgethi — информативный, мужской"),
    Voice("Laomedeia", "Laomedeia — бодрый, женский"),
    Voice("Achernar", "Achernar — мягкий, женский"),
    Voice("Alnilam", "Alnilam — твёрдый, мужской"),
    Voice("Schedar", "Schedar — ровный, мужской"),
    Voice("Gacrux", "Gacrux — зрелый, женский"),
    Voice("Pulcherrima", "Pulcherrima — напористый, женский"),
    Voice("Achird", "Achird — дружелюбный, мужской"),
    Voice("Zubenelgenubi", "Zubenelgenubi — непринуждённый, мужской"),
    Voice("Vindemiatrix", "Vindemiatrix — нежный, женский"),
    Voice("Sadachbia", "Sadachbia — живой, мужской"),
    Voice("Sadaltager", "Sadaltager — знающий, мужской"),
    Voice("Sulafat", "Sulafat — тёплый, женский"),
)
VOICE_IDS: frozenset[str] = frozenset(voice.id for voice in GEMINI_VOICES)


def is_voice(voice_id: str) -> bool:
    return voice_id in VOICE_IDS
