"""WhatsApp adapter over a self-hosted Evolution API v2.

Adapter talks to a thin EvolutionTransport; HTTP shape lives in HttpEvolutionTransport so
tests (and a future Cloud API) swap the transport only."""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Protocol

from sim.adapters.channels._http import API_TIMEOUT, MEDIA_MAX_BYTES
from sim.adapters.channels.capabilities import RECORD_VOICE, Capability
from sim.adapters.channels.wa_parse import WA_MEDIA_PREFIX
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import SendResult

WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
PRESENCE_MS = 5000          # how long one sendPresence keeps "печатает / записывает" visible

logger = logging.getLogger(__name__)


class EvolutionError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class EvolutionTransport(Protocol):
    async def request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> Any: ...


class HttpEvolutionTransport:
    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key

    async def request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> Any:
        import httpx  # noqa: PLC0415

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers={"apikey": self._key},
                timeout=API_TIMEOUT, follow_redirects=False,
            ) as c:
                r = await c.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise EvolutionError(f"evolution unreachable: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise EvolutionError(f"evolution {method} {path.split('/')[1]} failed "
                                 f"(HTTP {r.status_code})", status=r.status_code)
        try:
            return r.json()
        except ValueError:
            return {}


def instance_name(channel_id: int) -> str:
    return f"sim-{channel_id}"


def webhook_config(url: str, secret: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "url": url,
        "byEvents": False,
        "base64": False,
        "headers": {"apikey": secret},
        "events": list(WEBHOOK_EVENTS),
    }


def create_payload(instance: str, url: str, secret: str) -> dict[str, Any]:
    return {
        "instanceName": instance,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "readMessages": False,      # never tick the owner's chats as read
        "readStatus": False,
        "alwaysOnline": False,      # do not steal the phone's push notifications
        "syncFullHistory": False,
        "groupsIgnore": True,
        "webhook": webhook_config(url, secret),
    }


class WhatsAppAdapter:
    kind: ChannelKind = ChannelKind.WHATSAPP
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VOICE, Capability.CHAT_ACTION})

    def __init__(self, transport: EvolutionTransport, *, instance: str) -> None:
        self._t = transport
        self._instance = instance

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        try:
            raw = await self._t.request(
                "POST", f"/message/sendText/{self._instance}",
                {"number": external_thread_id, "text": text},
            )
        except EvolutionError as exc:
            return SendResult(ok=False, error=str(exc))
        key = (raw or {}).get("key") or {}
        return SendResult(ok=True, external_message_id=str(key.get("id") or "") or None)

    async def send_chat_action(self, external_thread_id: str, action: str) -> None:
        """Evolution v2 sendPresence: `composing` ("печатает") or `recording` ("записывает
        аудио") for a few seconds (best effort)."""
        presence = "recording" if action == RECORD_VOICE else "composing"
        try:
            await self._t.request(
                "POST", f"/chat/sendPresence/{self._instance}",
                {"number": external_thread_id, "presence": presence, "delay": PRESENCE_MS},
            )
        except Exception as exc:
            logger.debug("whatsapp presence failed: %s", type(exc).__name__)

    async def send_voice(
        self, external_thread_id: str, audio: bytes, duration_s: float | None
    ) -> SendResult:
        """Evolution v2 sendWhatsAppAudio: a push-to-talk note (`encoding` lets Evolution
        normalise the OGG/Opus for WhatsApp)."""
        try:
            raw = await self._t.request(
                "POST", f"/message/sendWhatsAppAudio/{self._instance}",
                {"number": external_thread_id,
                 "audio": base64.b64encode(audio).decode(), "encoding": True},
            )
        except EvolutionError as exc:
            return SendResult(ok=False, error=str(exc))
        key = (raw or {}).get("key") or {}
        return SendResult(ok=True, external_message_id=str(key.get("id") or "") or None)

    async def session_status(self) -> SessionStatus:
        try:
            raw = await self._t.request("GET", f"/instance/connectionState/{self._instance}")
        except EvolutionError as exc:
            return SessionStatus.EXPIRED if exc.status == 404 else SessionStatus.ERROR
        return map_state(connection_state_of(raw))

    async def download_media(self, ref: str) -> bytes:
        """Bytes for a `wa-msg:<message id>` marker (Evolution base64-media endpoint)."""
        if not ref.startswith(WA_MEDIA_PREFIX):
            raise ValueError("not a wa-msg ref")
        raw = await self._t.request(
            "POST", f"/chat/getBase64FromMediaMessage/{self._instance}",
            {"message": {"key": {"id": ref[len(WA_MEDIA_PREFIX):]}}, "convertToMp4": False},
        )
        b64 = (raw or {}).get("base64") or ""
        if len(b64) > MEDIA_MAX_BYTES * 4 // 3 + 16:
            raise ValueError("whatsapp media exceeds size bound")
        try:
            return base64.b64decode(b64.split(",", 1)[-1], validate=False)
        except (binascii.Error, ValueError):
            raise ValueError("whatsapp media: invalid base64") from None


def connection_state_of(raw: Any) -> str:
    inst = (raw or {}).get("instance") if isinstance(raw, dict) else None
    state = (inst or {}).get("state") if isinstance(inst, dict) else None
    return str(state or "close")


def map_state(state: str) -> SessionStatus:
    if state == "open":
        return SessionStatus.ACTIVE
    if state == "connecting":
        return SessionStatus.PENDING
    return SessionStatus.EXPIRED
