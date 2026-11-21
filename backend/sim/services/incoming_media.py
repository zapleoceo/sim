"""Opt-in queue integration. No paid transcription or implicit fallback."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import filetype

from sim.adapters.db.locks import NS_DIALOG, lock
from sim.services import instructions
from sim.services.app_settings import AppSettingsService
from sim.services.job_execution import require_live_execution
from sim.services.media_recognition import MAX_FILE_BYTES, UnsupportedMedia

if TYPE_CHECKING:
    from sim.services.media import MediaService


class IncomingMedia:
    def __init__(self, service: MediaService) -> None:
        self.s = service

    async def process(self, message_id: int, ref: str, kind: str) -> None:
        message = await self.s.messages.get(message_id)
        if message is None or message.media_status != "pending":
            return
        settings = await AppSettingsService(self.s.session).load()
        if not settings.incoming_media_subscription:
            await self.finish(message_id, "unsupported", "disabled")
            return
        if kind in ("audio", "voice"):
            await self.finish(message_id, "unsupported", "audio_not_supported")
            return
        dialog = await self.s.dialogs.get(message.dialog_id)
        if dialog is None:
            return
        channel = dialog.channel
        connector = self.s.registry.get(channel.kind)
        port = connector.build_port(channel.id, self.s.box.decrypt_json(channel.session_enc)) if connector else None
        download = getattr(port, "download_media_bounded", None)
        if download is None:
            await self.finish(message_id, "unsupported", "bounded_download_unavailable")
            return
        try:
            await require_live_execution()
            async with asyncio.timeout(45):
                data = await download(ref, max_bytes=MAX_FILE_BYTES)
            if not data or len(data) > MAX_FILE_BYTES:
                raise UnsupportedMedia("empty_or_oversized")
            result = await self.s.recognizer.recognize(data, kind)
        except (UnsupportedMedia, ValueError) as exc:
            # Only our allowlisted reason codes, never adapter errors/URLs/file contents.
            reason = str(exc) if isinstance(exc, UnsupportedMedia) else "download_limit_or_invalid"
            await self.finish(message_id, "unsupported", reason)
            return
        # No remote send. File storage is authenticated; documents never served as HTML.
        await require_live_execution()
        detected = filetype.guess(data)
        suffix = detected.extension if detected else "bin"
        name = await self.s.store.save(data, suffix)
        await self.finish(message_id, "done", None, result=result, media_url=f"/api/media/{name}")

    async def finish(self, message_id: int, status: str, reason: str | None,
                     *, result: str = "", media_url: str | None = None) -> None:
        await require_live_execution()
        message = await self.s.messages.get(message_id)
        if message is None:
            return
        await lock(self.s.session, NS_DIALOG, message.dialog_id)
        await self.s.session.refresh(message)
        if message.media_status != "pending":
            return
        message.media_status, message.media_reason = status, reason
        if media_url:
            message.media_url = media_url
        note = (instructions.text("attachment_note") + "\n" + result if status == "done"
                else instructions.text("attachment_failed_note"))
        message.text = f"{message.text}\n\n{note}".strip()
        await self.s.refresh_preview(message.dialog_id, message_id, message.text)
