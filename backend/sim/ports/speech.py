"""Speech port: text → voice. The only implementation is the AIbroker client
(`POST /v1/speech`, Google Gemini TTS on the Broker's paid Google account) — no provider keys
in Sim."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes                      # as the provider returned it (Gemini: WAV, 24 kHz mono)
    mime_type: str
    duration_s: float | None = None
    model: str | None = None
    cost_usd: float | None = None


class SpeechError(Exception):
    """The broker refused or failed the synthesis (no paid key, scope, provider error)."""


class SpeechPort(Protocol):
    async def synthesize(
        self, text: str, *, voice: str, style: str = "", workflow: str | None = None
    ) -> SpeechResult: ...
