"""Attachments of the admin preview chat: a recorded voice note, an audio file, a photo or a text
file becomes plain text the persona reads, the same way a channel voice note is replaced by its
transcript. Nothing is persisted except the media file itself, so the admin can play or open it.

Voice and audio go through the Broker transcription (`/v1/transcribe`), images through the
`vision` capability (logged in the request journal as workflow `preview-vision`)."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Literal, Protocol

from sim.adapters.media import MediaStore
from sim.ports.llm import LLMError, LLMResult

AttachmentKind = Literal["voice", "audio", "image", "file"]

MAX_BYTES = 10 * 1024 * 1024
TEXT_LIMIT = 8000
VISION_WORKFLOW = "preview-vision"
VISION_PROMPT = (
    "Опиши по-русски, что на изображении, коротко и по делу (2-5 предложений). "
    "Если на нём есть текст, перепиши его дословно."
)
TEXT_SUFFIXES = frozenset({"txt", "md", "csv", "json", "log", "html", "xml", "yaml", "yml"})
AUDIO_SUFFIXES = frozenset({"ogg", "oga", "opus", "mp3", "m4a", "wav", "webm", "aac", "flac"})
IMAGE_SUFFIXES = frozenset({"jpg", "jpeg", "png", "webp", "gif"})


class AttachmentError(Exception):
    """The file cannot be read (unsupported type, too large) or the Broker failed."""


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, filename: str) -> LLMResult: ...


class VisionChat(Protocol):
    async def chat(self, messages: list[dict[str, Any]], *, capability: str = ...,
                   workflow: str = ...) -> LLMResult: ...


@dataclass(frozen=True)
class Attachment:
    kind: AttachmentKind
    name: str
    text: str            # what the persona reads as the client's message
    media_url: str


def suffix_of(filename: str) -> str:
    return PurePath(filename).suffix.lstrip(".").lower()


def classify(filename: str, content_type: str, *, voice: bool) -> AttachmentKind:
    """Voice note recorded in the admin, an audio/image/text file, or unsupported (raises)."""
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    suffix = suffix_of(filename)
    if ctype.startswith("audio/") or suffix in AUDIO_SUFFIXES:
        return "voice" if voice else "audio"
    if ctype.startswith("image/") or suffix in IMAGE_SUFFIXES:
        return "image"
    if ctype.startswith("text/") or ctype == "application/json" or suffix in TEXT_SUFFIXES:
        return "file"
    raise AttachmentError("Поддерживаются голосовые, аудио, изображения и текстовые файлы")


def message_text(kind: AttachmentKind, name: str, content: str) -> str:
    """How the attachment reads in the dialog: a voice note is just its transcript."""
    if kind == "voice":
        return content
    if kind == "audio":
        return f"[аудио {name}: {content}]"
    if kind == "image":
        return f"[изображение: {content}]"
    return f"[файл {name}]\n{content}"


class PreviewAttachments:
    def __init__(self, transcriber: Transcriber, vision: VisionChat, store: MediaStore) -> None:
        self.transcriber = transcriber
        self.vision = vision
        self.store = store

    async def read(self, data: bytes, filename: str, content_type: str, *,
                   voice: bool = False) -> Attachment:
        if not data:
            raise AttachmentError("Пустой файл")
        if len(data) > MAX_BYTES:
            raise AttachmentError("Файл больше 10 МБ")
        kind = classify(filename, content_type, voice=voice)
        name = PurePath(filename).name[:120] or "file"
        content = await self.content(kind, data, name, content_type)
        stored = self.store.save(data, suffix_of(filename) or ("webm" if voice else "bin"))
        return Attachment(kind, name, message_text(kind, name, content), f"/api/media/{stored}")

    async def content(self, kind: AttachmentKind, data: bytes, name: str, content_type: str) -> str:
        try:
            if kind in ("voice", "audio"):
                text = (await self.transcriber.transcribe(data, name)).text.strip()
                return text or "[не удалось распознать речь]"
            if kind == "image":
                return await self.describe(data, content_type)
        except LLMError as exc:
            raise AttachmentError(f"Broker: {exc}") from exc
        return data.decode("utf-8", "replace").strip()[:TEXT_LIMIT] or "[пустой файл]"

    async def describe(self, data: bytes, content_type: str) -> str:
        mime = content_type if content_type.startswith("image/") else "image/jpeg"
        url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        result = await self.vision.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": url}},
            ]}],
            capability="vision", workflow=VISION_WORKFLOW,
        )
        return result.text.strip() or "[не удалось распознать изображение]"
