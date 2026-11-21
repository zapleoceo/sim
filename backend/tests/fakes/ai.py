"""Fakes of the model, media and queue ports (sim/ports.py, sim/services/queue.py). Each one
records its calls; model answers are scripted per model id."""
from __future__ import annotations

import inspect
import itertools
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sim.ports import (
    AudioResult,
    ChatMessage,
    ChatResult,
    LiveEvent,
    MediaResult,
    ModelInfo,
    PortError,
    Probe,
    Usage,
)

Answer = str | Exception | Callable[[dict[str, Any]], str | Awaitable[str]]


class FakeLlm:
    """`script(model, *answers)`: answers are returned in order for that model; an Exception is
    raised instead; a callable gets the call record and returns the text."""

    def __init__(self) -> None:
        self.answers: dict[str, deque[Answer]] = defaultdict(deque)
        self.defaults: dict[str, Answer] = {}
        self.calls: list[dict[str, Any]] = []
        self.models: list[ModelInfo] = []

    def script(self, model: str, *answers: Answer) -> FakeLlm:
        self.answers[model].extend(answers)
        return self

    def always(self, model: str, answer: Answer) -> FakeLlm:
        self.defaults[model] = answer
        return self

    def calls_for(self, model: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["model"] == model]

    async def chat(self, *, model: str, messages: Sequence[ChatMessage],
                   json_schema: dict[str, Any] | None = None, max_tokens: int = 4096,
                   label: str = "") -> ChatResult:
        record = {"model": model, "messages": list(messages), "json_schema": json_schema,
                  "label": label}
        self.calls.append(record)
        if self.answers[model]:
            answer = self.answers[model].popleft()
        elif model in self.defaults:
            answer = self.defaults[model]
        else:
            raise AssertionError(f"FakeLlm: no answer scripted for model {model!r}")
        if isinstance(answer, Exception):
            raise answer
        text = answer(record) if callable(answer) else answer
        if inspect.isawaitable(text):
            text = await text
        return ChatResult(text=text, usage=Usage(model=model, cost_usd=0.001, tokens_in=10,
                                                 tokens_out=5, latency_ms=12))

    async def list_models(self) -> list[ModelInfo]:
        return list(self.models)


@dataclass
class FakeTranscriber:
    text: str = "привет, это голосовое"
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def transcribe(self, *, audio: bytes, filename: str, model: str = "") -> ChatResult:
        self.calls.append({"audio": audio, "filename": filename, "model": model})
        if self.error is not None:
            raise self.error
        return ChatResult(text=self.text, usage=Usage(model=model or "stt"))


@dataclass
class FakeSpeech:
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def synthesize(self, *, text: str, voice: str, style: str, model: str) -> AudioResult:
        self.calls.append({"text": text, "voice": voice, "style": style, "model": model})
        if self.error is not None:
            raise self.error
        return AudioResult(data=b"RIFF" + text.encode(), mime="audio/wav", duration_s=2.0,
                           usage=Usage(model=model, cost_usd=0.002))


@dataclass
class FakeImageGen:
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_image(self, *, prompt: str, references: Sequence[tuple[bytes, str]],
                             model: str) -> MediaResult:
        self.calls.append({"prompt": prompt, "references": list(references), "model": model})
        if self.error is not None:
            raise self.error
        return MediaResult(data=b"PNG:" + prompt.encode(), mime="image/png",
                           usage=Usage(model=model, cost_usd=0.04, job_id="img-1"))


@dataclass
class FakeVideoGen:
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_video(self, *, prompt: str, first_frame: tuple[bytes, str] | None,
                             model: str, duration_s: int = 5) -> MediaResult:
        self.calls.append({"prompt": prompt, "first_frame": first_frame, "model": model,
                           "duration_s": duration_s})
        if self.error is not None:
            raise self.error
        return MediaResult(data=b"MP4:" + prompt.encode(), mime="video/mp4",
                           usage=Usage(model=model, cost_usd=0.5, job_id="vid-1"))


class FakeLiveSession:
    def __init__(self, events: Sequence[LiveEvent] = ()) -> None:
        self.sent: list[bytes] = []
        self._events = list(events)

    async def send_audio(self, pcm16k: bytes) -> None:
        self.sent.append(pcm16k)

    async def events(self) -> AsyncIterator[LiveEvent]:
        for event in self._events:
            yield event

    async def finish(self) -> dict[str, Any]:
        return {"cost_usd": 0.0}


@dataclass
class FakeLive:
    calls: list[dict[str, Any]] = field(default_factory=list)
    session: FakeLiveSession = field(default_factory=FakeLiveSession)

    async def open(self, *, model: str, system: str, voice: str, language: str,
                   greeting: str, max_seconds: int) -> FakeLiveSession:
        self.calls.append({"model": model, "system": system, "voice": voice,
                           "language": language, "greeting": greeting,
                           "max_seconds": max_seconds})
        return self.session


class MemoryStorage:
    """In-memory MediaStoragePort."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self._ids = itertools.count(1)

    async def put(self, data: bytes, *, mime: str, prefix: str) -> str:
        key = f"{prefix}/{next(self._ids):06d}"
        self.files[key] = data
        return key

    async def get(self, key: str) -> bytes:
        try:
            return self.files[key]
        except KeyError:
            raise PortError(f"no file {key}") from None

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


@dataclass
class FakeMediaTools:
    """Probe echoes the mime; documents are read as UTF-8 text unless `document` is set."""

    document: str | None = None
    duration_s: float | None = None
    video_note_error: Exception | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def probe(self, data: bytes, mime: str) -> Probe:
        self.calls.append(("probe", mime))
        return Probe(mime=mime, width=None, height=None, duration_s=self.duration_s)

    async def wav_to_ogg_opus(self, wav: bytes) -> tuple[bytes, float]:
        self.calls.append(("wav_to_ogg_opus", len(wav)))
        return b"OGG" + wav, 2.0

    async def to_pcm16k(self, data: bytes, mime: str) -> bytes:
        return data

    async def document_text(self, data: bytes, mime: str, file_name: str) -> str | None:
        self.calls.append(("document_text", file_name))
        if self.document is not None:
            return self.document
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    async def image_for_model(self, data: bytes, mime: str) -> tuple[bytes, str]:
        self.calls.append(("image_for_model", mime))
        return data, "image/jpeg"

    async def to_video_note(self, mp4: bytes) -> tuple[bytes, float]:
        self.calls.append(("to_video_note", len(mp4)))
        if self.video_note_error is not None:
            raise self.video_note_error
        return b"ROUND:" + mp4, 8.0


@dataclass
class RecordingQueue:
    """TaskQueue that only records what was deferred, in order."""

    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def reply(self, chat_id: int, delay_s: float = 0) -> None:
        self.calls.append(("reply", chat_id, delay_s))

    async def understand(self, media_id: int) -> None:
        self.calls.append(("understand", media_id))

    async def generate(self, generation_id: int) -> None:
        self.calls.append(("generate", generation_id))

    async def send(self, message_id: int, chat_id: int | None = None) -> None:
        self.calls.append(("send", message_id, chat_id))

    async def refresh_memory(self, chat_id: int) -> None:
        self.calls.append(("refresh_memory", chat_id))

    async def mark_read(self, message_id: int) -> None:
        self.calls.append(("mark_read", message_id))

    async def chat_action(self, chat_id: int, action: str, seconds: float) -> None:
        self.calls.append(("chat_action", chat_id, action, seconds))

    def actions(self) -> list[str]:
        """The chat actions asked for, in order."""
        return [call[2] for call in self.of("chat_action")]

    def of(self, kind: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == kind]
