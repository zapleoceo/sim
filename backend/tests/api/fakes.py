"""Minimal local fakes for the API tests: just what the routes touch, no network, no files."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.config import Settings
from sim.ports import ChatResult, ModelInfo, PortError, Probe, Usage

# A valid 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA"
    "60e6kgAAAABJRU5ErkJggg=="
)


class BorrowedSession:
    """`async with sessions() as s` that hands out the test session and never closes it."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class FakeLlm:
    models: list[ModelInfo] = field(default_factory=list)
    fail: bool = False
    # The only chat call of the API: describing the appearance by the reference photos.
    answer: str | None = None
    chats: list[dict[str, Any]] = field(default_factory=list)

    async def list_models(self) -> list[ModelInfo]:
        if self.fail:
            raise PortError("Broker недоступен", retryable=True)
        return list(self.models)

    async def chat(self, **kwargs: Any) -> ChatResult:
        if self.answer is None:
            raise AssertionError("no chat answer scripted for the API test")
        self.chats.append(kwargs)
        return ChatResult(text=self.answer, usage=Usage(model=str(kwargs.get("model", ""))))


class MemoryStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(self, data: bytes, *, mime: str, prefix: str) -> str:
        key = f"{prefix}/{len(self.files) + 1}"
        self.files[key] = data
        return key

    async def get(self, key: str) -> bytes:
        return self.files[key]

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


class FakeTools:
    async def probe(self, data: bytes, mime: str) -> Probe:
        return Probe(mime=mime, width=1, height=1) if mime.startswith("image/") else Probe(mime)

    async def wav_to_ogg_opus(self, wav: bytes, speed: float = 1.0) -> tuple[bytes, float]:
        return wav, 1.0

    async def voice_note(self, wav: bytes, ambience: str, seed: int,
                         speed: float = 1.0) -> tuple[bytes, float]:
        return wav, 1.0

    async def to_pcm16k(self, data: bytes, mime: str) -> bytes:
        return data

    async def document_text(self, data: bytes, mime: str, file_name: str) -> str | None:
        return None

    async def image_for_model(self, data: bytes, mime: str) -> tuple[bytes, str]:
        return data, mime

    async def video_frames(self, data: bytes, mime: str, count: int) -> list[tuple[bytes, str]]:
        return [(b"JPEG", "image/jpeg")] * count

    async def to_video_note(self, mp4: bytes) -> tuple[bytes, float]:
        return mp4, 1.0

    async def to_m4a(self, data: bytes, mime: str) -> bytes:
        return data

    async def call_recording(self, stereo_pcm: bytes, rate_hz: int) -> tuple[bytes, float]:
        return b"OggS" + stereo_pcm, round(len(stereo_pcm) / (4 * rate_hz), 2)


class RecordingTasks:
    """Records every task the API defers, with whether a DB transaction was still open."""

    def __init__(self, in_transaction: Callable[[], bool]) -> None:
        self.in_transaction = in_transaction
        self.calls: list[tuple[str, tuple[Any, ...], bool]] = []

    def record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args, self.in_transaction()))

    async def send(self, message_id: int, chat_id: int | None = None) -> None:
        self.record("send", message_id, chat_id)

    async def reply(self, chat_id: int, delay_s: float = 0) -> None:
        self.record("reply", chat_id, delay_s)

    async def understand(self, media_id: int) -> None:
        self.record("understand", media_id)

    async def generate(self, generation_id: int) -> None:
        self.record("generate", generation_id)

    async def refresh_memory(self, chat_id: int) -> None:
        self.record("refresh_memory", chat_id)

    async def update_passport(self, chat_id: int, delay_s: float = 0) -> None:
        self.record("update_passport", chat_id, delay_s)

    async def plan_day(self, persona_id: int, day: str, force: bool = False) -> None:
        self.record("plan_day", persona_id, day, force)

    async def summarize_day(self, chat_id: int, day: str, force: bool = False) -> None:
        self.record("summarize_day", chat_id, day, force)

    async def mark_read(self, message_id: int) -> None:
        self.record("mark_read", message_id)

    async def chat_action(self, chat_id: int, action: str, seconds: float) -> None:
        self.record("chat_action", chat_id, action, seconds)

    async def phone_check(self, persona_id: int, delay_s: float = 0) -> None:
        self.record("phone_check", persona_id, delay_s)

    async def look_at_phone(self, persona_id: int) -> None:
        self.record("look_at_phone", persona_id)

    async def poll_instagram(self, channel_id: int, delay_s: float = 0) -> None:
        self.record("poll_instagram", channel_id, delay_s)

    async def whatsapp_catch_up(self, channel_id: int) -> None:
        self.record("whatsapp_catch_up", channel_id)

    async def place_call(self, contact_id: int) -> None:
        self.record("place_call", contact_id)

    async def sync_stickers(self, channel_id: int) -> None:
        self.record("sync_stickers", channel_id)


@dataclass
class FakeDeps:
    """Duck-typed `sim.services.deps.Deps` with only what the API uses."""

    settings: Settings
    session: AsyncSession
    clock: FixedClock
    llm: FakeLlm = field(default_factory=FakeLlm)
    storage: MemoryStorage = field(default_factory=MemoryStorage)
    tools: FakeTools = field(default_factory=FakeTools)
    tasks: RecordingTasks = field(init=False)
    transcriber: Any = None
    speech: Any = None
    image_gen: Any = None
    video_gen: Any = None
    live: Any = None

    def uniform(self) -> float:
        return 0.5                      # the median of every human-like delay

    def __post_init__(self) -> None:
        self.tasks = RecordingTasks(self.session.in_transaction)

    def sessions(self) -> BorrowedSession:
        return BorrowedSession(self.session)
