"""Live voice port: a real-time spoken conversation (speech in, speech out).

The only implementation is the AIbroker client (WebSocket `/v1/live`, Google Gemini Live on the
Broker's paid Google account) — no provider keys in Sim. Audio is 16-bit little-endian mono PCM:
`input_rate_hz` towards the model, `output_rate_hz` back (Gemini: 16 kHz in, 24 kHz out)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

AUDIO = "audio"                # model speech (pcm)
TRANSCRIPT = "transcript"      # role + text of what was said, as it is recognised
INTERRUPTED = "interrupted"    # the caller talked over the model: drop the buffered speech
TURN_COMPLETE = "turn_complete"


@dataclass(frozen=True)
class LiveVoiceEvent:
    kind: str
    pcm: bytes = b""
    role: str = ""             # "user" | "assistant" (TRANSCRIPT)
    text: str = ""


@dataclass(frozen=True)
class LiveVoiceSummary:
    """What the Broker reports when the conversation ends."""

    transcript: list[tuple[str, str]] = field(default_factory=list)   # (role, text)
    duration_s: float | None = None
    cost_usd: float | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    end_reason: str = ""


class LiveVoiceError(Exception):
    """The broker refused or failed the session (no paid key, scope, cap, provider error)."""


class LiveVoiceSession(Protocol):
    input_rate_hz: int
    output_rate_hz: int

    async def send_audio(self, pcm: bytes) -> None: ...

    def events(self) -> AsyncIterator[LiveVoiceEvent]:
        """Model events until the conversation ends."""
        ...

    async def finish(self) -> LiveVoiceSummary:
        """Hang up (idempotent) and return the Broker's summary."""
        ...


class LiveVoicePort(Protocol):
    async def open(
        self, *, voice: str, system: str, language: str | None = None,
        greeting: str | None = None, workflow: str | None = None,
        max_seconds: float | None = None,
    ) -> LiveVoiceSession: ...
