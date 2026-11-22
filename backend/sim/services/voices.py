"""Gemini TTS prebuilt voices accepted by the Broker (`POST /v1/speech`). The persona page
offers this list (`GET /api/voices`, filtered by gender and searched by tone) and plays a sample
of each (`GET /api/voices/{id}/sample`); `persona.voice_id` must be one of the ids.

A sample is one short phrase spoken by the persona's TTS model, kept in this process
(`SampleCache`): the first listen costs one TTS call, the next ones nothing."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum

SAMPLE_TEXT = "Привет! Это мой голос. Как тебе, нравится? Если что, могу говорить и быстрее."
SAMPLE_CACHE_SIZE = 64


class VoiceGender(StrEnum):
    FEMALE = "female"
    MALE = "male"


GENDER_WORDS = {VoiceGender.FEMALE: "женский", VoiceGender.MALE: "мужской"}


@dataclass(frozen=True)
class Voice:
    id: str
    tone: str
    gender: VoiceGender

    @property
    def label(self) -> str:
        return f"{self.id} — {self.tone}, {GENDER_WORDS[self.gender]}"

    def view(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "gender": self.gender.value,
                "tone": self.tone}


F, M = VoiceGender.FEMALE, VoiceGender.MALE
GEMINI_VOICES: tuple[Voice, ...] = (
    Voice("Zephyr", "яркий", F),
    Voice("Puck", "бодрый", M),
    Voice("Charon", "информативный", M),
    Voice("Kore", "уверенный", F),
    Voice("Fenrir", "живой", M),
    Voice("Leda", "юный", F),
    Voice("Orus", "твёрдый", M),
    Voice("Aoede", "лёгкий", F),
    Voice("Callirrhoe", "спокойный", F),
    Voice("Autonoe", "яркий", F),
    Voice("Enceladus", "с придыханием", M),
    Voice("Iapetus", "чёткий", M),
    Voice("Umbriel", "спокойный", M),
    Voice("Algieba", "мягкий", M),
    Voice("Despina", "мягкий", F),
    Voice("Erinome", "чёткий", F),
    Voice("Algenib", "с хрипотцой", M),
    Voice("Rasalgethi", "информативный", M),
    Voice("Laomedeia", "бодрый", F),
    Voice("Achernar", "мягкий", F),
    Voice("Alnilam", "твёрдый", M),
    Voice("Schedar", "ровный", M),
    Voice("Gacrux", "зрелый", F),
    Voice("Pulcherrima", "напористый", F),
    Voice("Achird", "дружелюбный", M),
    Voice("Zubenelgenubi", "непринуждённый", M),
    Voice("Vindemiatrix", "нежный", F),
    Voice("Sadachbia", "живой", M),
    Voice("Sadaltager", "знающий", M),
    Voice("Sulafat", "тёплый", F),
)
VOICE_IDS: frozenset[str] = frozenset(voice.id for voice in GEMINI_VOICES)


def is_voice(voice_id: str) -> bool:
    return voice_id in VOICE_IDS


class SampleCache:
    """WAV samples by (TTS model, voice), the oldest dropped past `SAMPLE_CACHE_SIZE`."""

    def __init__(self, size: int = SAMPLE_CACHE_SIZE) -> None:
        self.size = size
        self.items: OrderedDict[tuple[str, str], tuple[bytes, str]] = OrderedDict()

    def get(self, model: str, voice: str) -> tuple[bytes, str] | None:
        found = self.items.get((model, voice))
        if found is not None:
            self.items.move_to_end((model, voice))
        return found

    def put(self, model: str, voice: str, data: bytes, mime: str) -> None:
        self.items[(model, voice)] = (data, mime)
        self.items.move_to_end((model, voice))
        while len(self.items) > self.size:
            self.items.popitem(last=False)


SAMPLES = SampleCache()
