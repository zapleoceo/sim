"""`MessengerPort` for WhatsApp over Evolution API v2 (docs/channels.md, «Отправка»).

- text: `POST /message/sendText/{inst}` {number, text};
- voice: `POST /message/sendWhatsAppAudio/{inst}` {number, audio: base64, encoding} — a PTT
  note, not an audio file;
- photo / video / file / sticker: `POST /message/sendMedia/{inst}` {number, mediatype,
  mimetype, media: base64, caption, fileName};
- video note: `POST /message/sendPtv/{inst}` {number, video: base64} — WhatsApp's round
  «video message» (PTV), the same square 640×640 mp4 SIM makes for Telegram, no caption
  (neither has one).

Statuses: `POST /chat/sendPresence/{inst}` — `composing` («печатает…»), `recording`
(«записывает аудио…»), `paused` to stop; read: `POST /chat/markMessageAsRead/{inst}` (blue
ticks). The address is the chat's jid (`Peer.thread_id`). Evolution's 4xx is permanent, 5xx and
«unreachable» are retried (sim/channels/whatsapp/evolution.py)."""
from __future__ import annotations

import base64
from typing import Any

from sim.channels.store import ChannelStore
from sim.channels.whatsapp.evolution import EvolutionTransport, instance_name
from sim.domain.enums import ChannelKind
from sim.domain.presence import ChatAction
from sim.ports import OutboundMessage, Peer, PortError

PRESENCE_MS = 5000
MEDIA_TYPES = {"photo": "image", "video": "video", "file": "document", "sticker": "image"}
# WhatsApp shows «записывает аудио» only for audio; a round video is «печатает» like the rest.
PRESENCE = {ChatAction.CANCEL.value: "paused", ChatAction.RECORD_AUDIO.value: "recording"}


def number_of(peer: Peer) -> str:
    jid = peer.thread_id or peer.external_id
    if not jid:
        raise PortError("the chat has no WhatsApp address")
    return jid


def message_id(answer: Any) -> str | None:
    key = answer.get("key") if isinstance(answer, dict) else None
    found = key.get("id") if isinstance(key, dict) else None
    return str(found) if found else None


class WhatsAppMessenger:
    def __init__(self, api: EvolutionTransport, store: ChannelStore) -> None:
        self.api = api
        self.store = store

    async def _instance(self, channel_id: int) -> str:
        account = await self.store.account(channel_id, ChannelKind.WHATSAPP)
        return str(account.credentials.get("instance") or instance_name(channel_id))

    async def send(self, channel_id: int, message: OutboundMessage) -> str | None:
        number = number_of(message.peer)
        if message.kind == "text":
            path, body = "sendText", {"number": number, "text": message.text}
        elif message.kind == "video_note":
            path, body = "sendPtv", {"number": number, "video": _b64(message)}
        elif message.kind == "voice":
            path, body = "sendWhatsAppAudio", {"number": number, "audio": _b64(message),
                                               "encoding": True}
        elif message.kind in MEDIA_TYPES:
            path, body = "sendMedia", {
                "number": number, "mediatype": MEDIA_TYPES[message.kind],
                "mimetype": message.mime or "application/octet-stream",
                "media": _b64(message), "caption": message.text,
                "fileName": message.file_name or f"{message.kind}"}
        else:
            raise PortError(f"WhatsApp can not send {message.kind}")
        instance = await self._instance(channel_id)
        return message_id(await self.api.request("POST", f"/message/{path}/{instance}", body))

    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
        instance = await self._instance(channel_id)
        await self.api.request("POST", f"/chat/sendPresence/{instance}", {
            "number": number_of(peer), "presence": PRESENCE.get(action, "composing"),
            "delay": PRESENCE_MS})

    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None:
        instance = await self._instance(channel_id)
        await self.api.request("POST", f"/chat/markMessageAsRead/{instance}", {
            "readMessages": [{"remoteJid": number_of(peer), "fromMe": False,
                              "id": external_id}]})


def _b64(message: OutboundMessage) -> str:
    if not message.data:
        raise PortError(f"{message.kind} message has no data")
    return base64.b64encode(message.data).decode()
