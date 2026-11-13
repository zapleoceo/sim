"""Telegram Bot API adapter (httpx, no library).

The adapter maps domain types onto a thin TelegramTransport; only the transport touches
httpx. The bot token lives in the request URL (Telegram's design), so it must never reach a
log or an exception message: transport errors carry only Telegram's own `description`."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from sim.adapters.channels._http import API_TIMEOUT, download_bounded
from sim.adapters.channels.capabilities import Capability
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import InboundMessage, SendResult

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
TG_MAX_TEXT = 4096
TG_FILE_PREFIX = "tg-file:"
TG_MAX_FILE_BYTES = 20 * 1024 * 1024   # Bot API getFile ceiling


class TelegramError(Exception):
    """Bot API said ok=false, or the network failed. Message never contains the token."""

    def __init__(self, message: str, *, code: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class TelegramTransport(Protocol):
    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any: ...

    async def download_file(self, file_path: str, max_bytes: int) -> bytes: ...

    async def upload(
        self, method: str, params: dict[str, Any], field: str, filename: str, data: bytes,
        mime: str,
    ) -> Any: ...


def parse_answer(method: str, r: Any) -> Any:
    """Bot API envelope → result, or TelegramError with Telegram's own description."""
    try:
        body = r.json()
    except ValueError:
        raise TelegramError(f"{method}: non-JSON answer (HTTP {r.status_code})") from None
    if not isinstance(body, dict) or not body.get("ok"):
        body = body if isinstance(body, dict) else {}
        retry = (body.get("parameters") or {}).get("retry_after")
        raise TelegramError(
            str(body.get("description") or f"{method} failed (HTTP {r.status_code})"),
            code=body.get("error_code") or r.status_code,
            retry_after=int(retry) if retry else None,
        )
    return body.get("result")


class BotApiTransport:
    def __init__(self, bot_token: str, *, base_url: str = TG_API) -> None:
        self._token = bot_token
        self._base = base_url.rstrip("/")

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        import httpx  # noqa: PLC0415

        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=False) as c:
                r = await c.post(f"{self._base}/bot{self._token}/{method}", json=params or {})
        except httpx.HTTPError as exc:
            # str(exc) may embed the URL (token): name the type only.
            raise TelegramError(
                f"network error calling {method}: {type(exc).__name__}"
            ) from None
        return parse_answer(method, r)

    async def upload(
        self, method: str, params: dict[str, Any], field: str, filename: str, data: bytes,
        mime: str,
    ) -> Any:
        """multipart/form-data call (sendVoice & co.): the file goes in `field`."""
        import httpx  # noqa: PLC0415

        form = {k: str(v) for k, v in params.items()}
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT, follow_redirects=False) as c:
                r = await c.post(f"{self._base}/bot{self._token}/{method}", data=form,
                                 files={field: (filename, data, mime)})
        except httpx.HTTPError as exc:
            raise TelegramError(
                f"network error calling {method}: {type(exc).__name__}"
            ) from None
        return parse_answer(method, r)

    async def download_file(self, file_path: str, max_bytes: int) -> bytes:
        try:
            return await download_bounded(
                f"{self._base}/file/bot{self._token}/{file_path}", max_bytes=max_bytes
            )
        except ValueError:
            raise
        except Exception as exc:
            raise TelegramError(f"file download failed: {type(exc).__name__}") from None


def chunk_text(text: str, limit: int = TG_MAX_TEXT) -> list[str]:
    """Split on newline / space boundaries; hard-cut only when there is none."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n"), window.rfind(" "))
        if cut < limit // 2:
            out.append(rest[:limit])
            rest = rest[limit:]
        else:
            out.append(rest[:cut])
            rest = rest[cut + 1:]
    if rest:
        out.append(rest)
    return out


class TelegramAdapter:
    kind: ChannelKind = ChannelKind.TELEGRAM
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VOICE, Capability.SEND_ATTACHMENT,
                              Capability.CHAT_ACTION})

    def __init__(self, transport: TelegramTransport) -> None:
        self._t = transport

    async def _send_one(self, chat_id: str, part: str) -> Any:
        params = {"chat_id": chat_id, "text": part}
        try:
            return await self._t.call("sendMessage", params)
        except TelegramError as exc:
            if not exc.retry_after or exc.retry_after > 5:
                raise
            await asyncio.sleep(exc.retry_after)
            return await self._t.call("sendMessage", params)

    async def send_chat_action(self, external_thread_id: str, action: str) -> None:
        """Bot API sendChatAction: `typing` / `record_voice` for ~5 s (best effort)."""
        try:
            await self._t.call("sendChatAction", {"chat_id": external_thread_id, "action": action})
        except Exception as exc:
            logger.debug("telegram chat action failed: %s", type(exc).__name__)

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        """Long texts go out in chunks. A failure on the FIRST chunk is a failed send (nothing
        reached the client). A failure on a later chunk is a partial delivery: reporting it as
        failed would make the retry resend every chunk and duplicate the text, so it is
        logged and reported as sent (the first message id is returned)."""
        first_id: str | None = None
        parts = chunk_text(text)
        for index, part in enumerate(parts):
            try:
                res = await self._send_one(external_thread_id, part)
            except TelegramError as exc:
                if index == 0:
                    return SendResult(ok=False, error=str(exc))
                logger.warning(
                    "telegram partial delivery: chunk %d/%d failed (%s); not retrying",
                    index + 1, len(parts), exc,
                )
                break
            first_id = first_id or str((res or {}).get("message_id", "")) or None
        return SendResult(ok=True, external_message_id=first_id)

    async def send_voice(
        self, external_thread_id: str, audio: bytes, duration_s: float | None
    ) -> SendResult:
        """sendVoice with an OGG/Opus note (Telegram renders it as a voice bubble)."""
        params: dict[str, Any] = {"chat_id": external_thread_id}
        if duration_s:
            params["duration"] = max(1, round(duration_s))
        try:
            res = await self._t.upload("sendVoice", params, "voice", "voice.ogg", audio,
                                       "audio/ogg")
        except TelegramError as exc:
            return SendResult(ok=False, error=str(exc))
        return SendResult(ok=True,
                          external_message_id=str((res or {}).get("message_id", "")) or None)

    async def send_attachment(
        self, external_thread_id: str, data: bytes, filename: str,
        mime: str, caption: str,
    ) -> SendResult:
        method = "sendPhoto" if mime.startswith("image/") else "sendDocument"
        field = "photo" if method == "sendPhoto" else "document"
        try:
            res = await self._t.upload(
                method, {"chat_id": external_thread_id, "caption": caption[:1000]},
                field, filename, data, mime,
            )
        except TelegramError as exc:
            return SendResult(ok=False, error=str(exc))
        return SendResult(ok=True,
                          external_message_id=str((res or {}).get("message_id", "")) or None)

    async def session_status(self) -> SessionStatus:
        try:
            await self._t.call("getMe")
        except TelegramError as exc:
            # 401 = token revoked. Anything else (network, 5xx) is inconclusive: stay active.
            return SessionStatus.ERROR if exc.code in (401, 404) else SessionStatus.ACTIVE
        return SessionStatus.ACTIVE

    async def download_media(self, ref: str) -> bytes:
        """Bytes for a `tg-file:<file_id>` marker produced by parse_update."""
        if not ref.startswith(TG_FILE_PREFIX):
            raise ValueError("not a tg-file ref")
        info = await self._t.call("getFile", {"file_id": ref[len(TG_FILE_PREFIX):]})
        size = int((info or {}).get("file_size") or 0)
        if size > TG_MAX_FILE_BYTES:
            raise ValueError(f"telegram file too large: {size} bytes")
        path = (info or {}).get("file_path")
        if not path:
            raise TelegramError("getFile returned no file_path")
        return await self._t.download_file(path, TG_MAX_FILE_BYTES)


def _media_of(msg: dict[str, Any]) -> tuple[str | None, str | None]:
    if msg.get("voice"):
        return "voice", f"{TG_FILE_PREFIX}{msg['voice'].get('file_id')}"
    if msg.get("audio"):
        return "audio", f"{TG_FILE_PREFIX}{msg['audio'].get('file_id')}"
    photos = msg.get("photo")
    if isinstance(photos, list) and photos:
        return "image", f"{TG_FILE_PREFIX}{photos[-1].get('file_id')}"   # largest size last
    for key in ("video", "video_note"):
        if msg.get(key):
            return "video", f"{TG_FILE_PREFIX}{msg[key].get('file_id')}"
    return None, None


def parse_update(payload: Any) -> list[InboundMessage]:
    """Bot API Update to InboundMessage list. Private chats only."""
    if not isinstance(payload, dict):
        return []
    edited = False
    msg = payload.get("message")
    if not isinstance(msg, dict):
        msg = payload.get("edited_message")
        edited = True
    if not isinstance(msg, dict):
        return []
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    if chat.get("type") != "private" or sender.get("is_bot") or chat.get("id") is None:
        return []
    kind, url = _media_of(msg)
    text = msg.get("text") or msg.get("caption") or ""
    if not text and not kind:
        return []
    ts = msg.get("date")
    ext = f"{chat['id']}:{msg.get('message_id')}"
    if edited:
        ext += f":e{msg.get('edit_date')}"
        ts = msg.get("edit_date") or ts
    name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or None
    uname = sender.get("username")
    return [
        InboundMessage(
            external_thread_id=str(chat["id"]),
            external_user_id=str(sender.get("id", chat["id"])),
            text=text,
            occurred_at=(
                datetime.fromtimestamp(int(ts), tz=UTC).replace(tzinfo=None)
                if ts else datetime.now(UTC).replace(tzinfo=None)
            ),
            external_id=ext,
            user_name=name,
            user_handle=f"@{uname}" if uname else None,
            media_kind=kind,
            media_url=url,
            is_edit=edited,
        )
    ]
