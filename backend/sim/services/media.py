"""Attachment processing, run by the `media` job (never inside the webhook request).

A channel attachment is a reference (`tg-file:<id>`, `wa-msg:<id>`, an https URL), not a URL a
plain GET can fetch: bytes come from the channel port's `download_media` (Capability
DOWNLOAD_MEDIA). A voice message is then transcribed and its text replaces the placeholder the
message was stored with. The reply job waits for open media jobs of the dialog, so the model
always sees the transcript."""
from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.capabilities import Capability
from sim.adapters.crypto import SecretBox
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.media import MediaStore
from sim.connectors.spec import Connector
from sim.domain.enums import ChannelKind
from sim.ports.llm import LLMError, LLMPort

log = logging.getLogger(__name__)

VOICE_KINDS = ("audio", "voice")
MEDIA_KINDS = (*VOICE_KINDS, "image")
VOICE_FAILED = "[голосовое сообщение: не удалось распознать]"
PLACEHOLDERS = {
    "image": "[изображение]",
    "video": "[видео]",
    "audio": "[аудио]",
    "voice": "[голосовое сообщение]",
}
MEDIA_MAX_ATTEMPTS = 3


def placeholder_for(kind: str | None) -> str:
    return PLACEHOLDERS.get(kind or "", "[вложение]")


def guess_suffix(kind: str | None) -> str:
    return {"image": "jpg", "video": "mp4", "audio": "ogg", "voice": "ogg"}.get(kind or "", "bin")


class MediaError(Exception):
    """Download or transcription failed; the job retries with backoff."""


class MediaService:
    def __init__(
        self,
        session: AsyncSession,
        registry: Mapping[ChannelKind, Connector],
        box: SecretBox,
        llm: LLMPort,
        store: MediaStore,
    ) -> None:
        self.registry = registry
        self.box = box
        self.llm = llm
        self.store = store
        self.messages = MessageRepo(session)
        self.dialogs = DialogRepo(session)

    async def download(self, dialog_id: int, ref: str) -> bytes:
        dialog = await self.dialogs.get(dialog_id)
        if dialog is None:
            raise MediaError("dialog not found")
        channel = dialog.channel
        connector = self.registry.get(channel.kind)
        if connector is None:
            raise MediaError(f"no connector for {channel.kind}")
        port = connector.build_port(channel.id, self.box.decrypt_json(channel.session_enc))
        if Capability.DOWNLOAD_MEDIA not in getattr(port, "capabilities", frozenset()):
            raise MediaError(f"{channel.kind} cannot download media")
        try:
            return await port.download_media(ref)  # type: ignore[attr-defined]
        except Exception as exc:  # adapters raise heterogeneous errors; never log the ref
            raise MediaError(f"download failed: {type(exc).__name__}") from exc

    async def process(self, message_id: int, ref: str, kind: str, *, transcribe: bool) -> None:
        message = await self.messages.get(message_id)
        if message is None:
            return
        data = await self.download(message.dialog_id, ref)
        name = self.store.save(data, guess_suffix(kind))
        message.media_url = f"/api/media/{name}"
        if transcribe:
            message.text = await self.transcribe(data)
        await self.refresh_preview(message.dialog_id, message_id, message.text)

    async def transcribe(self, audio: bytes) -> str:
        try:
            result = await self.llm.transcribe(audio, "voice.ogg")
        except LLMError as exc:
            raise MediaError(f"transcription failed: {exc}") from exc
        return result.text.strip() or VOICE_FAILED

    async def give_up(self, message_id: int, kind: str, *, transcribe: bool) -> None:
        """Out of attempts: the dialog continues with an honest placeholder."""
        message = await self.messages.get(message_id)
        if message is None:
            return
        if transcribe:
            message.text = VOICE_FAILED
            await self.refresh_preview(message.dialog_id, message_id, message.text)

    async def refresh_preview(self, dialog_id: int, message_id: int, text: str) -> None:
        dialog = await self.dialogs.get(dialog_id)
        last = await self.messages.last(dialog_id)
        if dialog is not None and last is not None and last.id == message_id:
            dialog.last_message_text = text[:300]
