"""Interfaces between services and the outside world. Services depend only on these; adapters
implement them (sim/adapters/*); tests replace them with fakes (tests/fakes.py).

Errors: every port raises `PortError` (or a subclass) with a short human text; `retryable`
tells the task runner whether to try again."""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class PortError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


# --- models (AI Broker) ----------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    id: str
    available: bool
    input_modalities: tuple[str, ...]       # text, image, audio, video, pdf
    output_modalities: tuple[str, ...]      # text, image, audio, video
    capabilities: tuple[str, ...]           # Broker capability names
    price: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    model: str
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class ChatPart:
    """One block of a user message: text, or an inline file (image/video/pdf) as bytes."""

    text: str | None = None
    data: bytes | None = None
    mime: str | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: str                       # system | user | assistant
    parts: Sequence[ChatPart]


@dataclass(frozen=True)
class ChatResult:
    text: str
    usage: Usage
    raw: dict[str, Any] = field(default_factory=dict)


class LlmPort(Protocol):
    async def chat(self, *, model: str, messages: Sequence[ChatMessage],
                   json_schema: dict[str, Any] | None = None, max_tokens: int = 4096,
                   label: str = "") -> ChatResult: ...

    async def list_models(self) -> list[ModelInfo]: ...


class TranscriberPort(Protocol):
    async def transcribe(self, *, audio: bytes, filename: str, model: str = "") -> ChatResult: ...


@dataclass(frozen=True)
class AudioResult:
    data: bytes                     # WAV from the Broker
    mime: str
    duration_s: float | None
    usage: Usage


class SpeechPort(Protocol):
    async def synthesize(self, *, text: str, voice: str, style: str, model: str) -> AudioResult: ...


@dataclass(frozen=True)
class MediaResult:
    data: bytes
    mime: str
    usage: Usage


class ImageGenPort(Protocol):
    async def generate_image(self, *, prompt: str, references: Sequence[tuple[bytes, str]],
                             model: str) -> MediaResult: ...


class VideoGenPort(Protocol):
    async def generate_video(self, *, prompt: str, first_frame: tuple[bytes, str] | None,
                             model: str, duration_s: int = 5) -> MediaResult: ...


# --- live voice (calls) ----------------------------------------------------------------------


@dataclass(frozen=True)
class LiveEvent:
    # audio | transcript_in | transcript_out | interrupted (caller talked over: drop queued
    # audio) | done | error
    kind: str
    audio: bytes = b""              # 24 kHz mono s16le PCM from the model
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class LiveSession(Protocol):
    async def send_audio(self, pcm16k: bytes) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...
    async def finish(self) -> dict[str, Any]: ...


class LivePort(Protocol):
    async def open(self, *, model: str, system: str, voice: str, language: str,
                   greeting: str, max_seconds: int) -> LiveSession: ...


# --- messenger -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Peer:
    external_id: str
    access_hash: str | None = None
    name: str = ""
    username: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class InboundAttachment:
    kind: str                       # photo | video | voice | video_note | file | sticker | audio
    data: bytes
    mime: str
    file_name: str = ""
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class InboundMessage:
    channel_id: int
    peer: Peer
    external_id: str
    text: str
    sent_at: datetime
    attachment: InboundAttachment | None = None
    reply_to_external_id: str | None = None


@dataclass(frozen=True)
class OutboundMessage:
    peer: Peer
    kind: str                       # text | photo | video | voice | video_note | file
    text: str = ""
    data: bytes | None = None
    mime: str | None = None
    file_name: str = ""
    duration_s: float | None = None


class MessengerPort(Protocol):
    async def send(self, channel_id: int, message: OutboundMessage) -> str: ...
    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None: ...
    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None: ...


# --- storage and media tools -----------------------------------------------------------------


class MediaStoragePort(Protocol):
    async def put(self, data: bytes, *, mime: str, prefix: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class Probe:
    mime: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


class MediaToolsPort(Protocol):
    async def probe(self, data: bytes, mime: str) -> Probe: ...
    async def wav_to_ogg_opus(self, wav: bytes) -> tuple[bytes, float]: ...
    async def to_pcm16k(self, data: bytes, mime: str) -> bytes: ...
    async def document_text(self, data: bytes, mime: str, file_name: str) -> str | None: ...
    async def image_for_model(self, data: bytes, mime: str) -> tuple[bytes, str]: ...
    async def to_video_note(self, mp4: bytes) -> tuple[bytes, float]:
        """A round video message: square 640x640, H.264 + AAC, at most 60 s. (bytes, seconds)"""
        ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...
