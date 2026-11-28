"""Evolution API v2 payloads -> SIM. Pure, no I/O.

- Text is read from every bubble a person reads: `conversation`, `extendedTextMessage.text`
  (replies, links), media captions, button / list replies; a bubble without text gets a line:
  a reaction is its emoji (on the message it reacts to), a location «📍 геопозиция» + a map
  link, a contact card «👤 контакт: имя». A reaction taken back (empty emoji) is skipped.
- What a message answers: `contextInfo.stanzaId` of ANY part (a quoted photo or voice note
  too, not only a text).
- Envelopes are peeled first: disappearing (`ephemeralMessage`), view-once
  (`viewOnceMessage`, `viewOnceMessageV2`), a document with a caption.
- Media: image -> photo, sticker (static or animated WebP) -> sticker, round video message
  (`ptvMessage`, кружок) -> video_note, video -> video, audio -> voice (a PTT note) or audio,
  document -> file; the bytes come in the event (`webhook.base64`), else the attachment is
  empty and the Inbox still knows something arrived. Until 2026-11-28 `ptvMessage`, reactions,
  locations and contacts were not read: such a message was dropped as empty.
- Addresses: `<digits>@s.whatsapp.net` is the phone itself; `@lid` is WhatsApp's privacy id
  with no phone in it — the phone rides in `remoteJidAlt` when WhatsApp gives it. The client's
  address is the phone jid when known, else the jid as it came. Groups (`@g.us`), broadcasts
  and status updates are not chats with a person and are skipped; so are our own messages
  (`fromMe`: SIM records its sends itself)."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sim.ports import InboundAttachment, InboundMessage, Peer

MEDIA_MAX_BYTES = 20 * 1024 * 1024
ENVELOPES = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
             "viewOnceMessageV2Extension", "documentWithCaptionMessage")
TEXT_KEYS: tuple[tuple[str, str], ...] = (
    ("conversation", ""),
    ("extendedTextMessage", "text"),
    ("imageMessage", "caption"),
    ("videoMessage", "caption"),
    ("documentMessage", "caption"),
    ("buttonsResponseMessage", "selectedDisplayText"),
    ("listResponseMessage", "title"),
    ("templateButtonReplyMessage", "selectedDisplayText"),
)
# message key -> (SIM attachment kind, default mime)
MEDIA_KINDS: tuple[tuple[str, str, str], ...] = (
    ("imageMessage", "photo", "image/jpeg"),
    ("stickerMessage", "sticker", "image/webp"),
    ("ptvMessage", "video_note", "video/mp4"),      # the round «video message» (кружок)
    ("videoMessage", "video", "video/mp4"),
    ("audioMessage", "voice", "audio/ogg"),
    ("documentMessage", "file", "application/octet-stream"),
)
PHONE_DOMAIN = "s.whatsapp.net"
MAPS = "https://maps.google.com/?q={lat},{lng}"


@dataclass(frozen=True)
class WebhookEvent:
    kind: str                                   # messages | connection | other
    records: list[dict[str, Any]] = field(default_factory=list)
    state: str = ""                             # connection: open | connecting | close
    reason: int | None = None                   # connection: statusReason (401 = logged out)


def unwrap(message: Any) -> dict[str, Any]:
    body = message if isinstance(message, dict) else {}
    for _ in range(4):
        for envelope in ENVELOPES:
            inner = body.get(envelope)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                body = inner["message"]
                break
        else:
            return body
    return body


def text_of(message: Any) -> str:
    body = unwrap(message)
    for key, sub in TEXT_KEYS:
        value = body.get(key)
        if not sub and isinstance(value, str):
            return value
        if sub and isinstance(value, dict) and isinstance(value.get(sub), str):
            return value[sub]
    return card_text(body)


def card_text(body: dict[str, Any]) -> str:
    """A line for a bubble without text of its own: a reaction (its emoji), a location, a
    contact card. "" for anything else."""
    reaction = body.get("reactionMessage")
    if isinstance(reaction, dict):
        return str(reaction.get("text") or "")      # "" = the reaction was taken back
    for key in ("locationMessage", "liveLocationMessage"):
        place = body.get(key)
        if isinstance(place, dict):
            lat, lng = place.get("degreesLatitude"), place.get("degreesLongitude")
            name = " · ".join(str(place[k]).strip() for k in ("name", "address")
                              if str(place.get(k) or "").strip())
            link = MAPS.format(lat=lat, lng=lng) if lat is not None and lng is not None else ""
            return " ".join(part for part in ("📍 геопозиция", name, link) if part)
    contact = body.get("contactMessage")
    if isinstance(contact, dict):
        return f"👤 контакт: {contact.get('displayName') or ''}".strip()
    contacts = body.get("contactsArrayMessage")
    if isinstance(contacts, dict):
        names = [str(c.get("displayName") or "") for c in contacts.get("contacts") or []
                 if isinstance(c, dict)]
        return f"👤 контакты: {', '.join(n for n in names if n)}".strip()
    return ""


def reply_to_of(message: Any) -> str | None:
    """The message this one answers: `contextInfo.stanzaId` of any part (a text, a photo, a
    voice note can all quote), or the message a reaction is on."""
    body = unwrap(message)
    reaction = body.get("reactionMessage")
    if isinstance(reaction, dict):
        target = (reaction.get("key") or {}).get("id")
        return str(target) if target else None
    for part in body.values():
        context = part.get("contextInfo") if isinstance(part, dict) else None
        if isinstance(context, dict) and context.get("stanzaId"):
            return str(context["stanzaId"])
    return None


def media_of(message: Any) -> tuple[str, dict[str, Any], str] | None:
    """(kind, the media message, default mime) or None."""
    body = unwrap(message)
    for key, kind, mime in MEDIA_KINDS:
        media = body.get(key)
        if isinstance(media, dict):
            if kind == "voice" and not media.get("ptt", True):
                kind = "audio"
            return kind, media, mime
    return None


def phone_of(jid: str) -> str | None:
    """+<digits> of a plain WhatsApp address; None for @lid, groups and anything else."""
    local, _, domain = str(jid or "").partition("@")
    digits = local.split(":")[0]
    if domain != PHONE_DOMAIN or not digits.isdigit():
        return None
    return f"+{digits}"


def address_of(key: dict[str, Any]) -> str:
    """The client's jid: the phone jid when WhatsApp gives it (also for an @lid chat)."""
    alt = str(key.get("remoteJidAlt") or "")
    return alt if phone_of(alt) else str(key.get("remoteJid") or "")


def is_person(jid: str) -> bool:
    return bool(jid) and not jid.endswith("@g.us") and "@broadcast" not in jid \
        and "@newsletter" not in jid


def sent_at(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC)


def decode_media(record: dict[str, Any]) -> bytes:
    """The media bytes Evolution put into the event (`message.base64`); b"" when absent."""
    message = record.get("message")
    raw = (message.get("base64") if isinstance(message, dict) else None) \
        or record.get("base64") or ""
    if not isinstance(raw, str) or not raw or len(raw) > MEDIA_MAX_BYTES * 4 // 3 + 16:
        return b""
    try:
        return base64.b64decode(raw.split(",", 1)[-1], validate=False)
    except (binascii.Error, ValueError):
        return b""


def inbound_of(channel_id: int, record: Any) -> InboundMessage | None:
    """One Evolution message record -> an incoming client message; None to skip it."""
    if not isinstance(record, dict):
        return None
    key = record.get("key") if isinstance(record.get("key"), dict) else {}
    jid = address_of(key)
    message_id = str(key.get("id") or "")
    if key.get("fromMe") or not is_person(str(key.get("remoteJid") or "")) or not message_id:
        return None
    message = record.get("message")
    media = media_of(message)
    text = text_of(message)
    if not text and media is None:
        return None
    attachment = None
    if media is not None:
        kind, info, mime = media
        seconds = info.get("seconds")
        attachment = InboundAttachment(
            kind=kind, data=decode_media(record),
            mime=str(info.get("mimetype") or mime).split(";")[0].strip(),
            file_name=str(info.get("fileName") or ""),
            duration_s=float(seconds) if isinstance(seconds, int | float) else None,
            width=info.get("width") if isinstance(info.get("width"), int) else None,
            height=info.get("height") if isinstance(info.get("height"), int) else None)
    peer = Peer(external_id=jid, name=str(record.get("pushName") or ""),
                phone=phone_of(jid) or phone_of(str(key.get("remoteJid") or "")),
                thread_id=jid)
    return InboundMessage(channel_id=channel_id, peer=peer, external_id=message_id, text=text,
                          sent_at=sent_at(record.get("messageTimestamp")),
                          attachment=attachment, reply_to_external_id=reply_to_of(message))


def records_of(data: Any) -> list[dict[str, Any]]:
    """`data` of messages.upsert / findMessages: one record, a list, or {messages: [...] |
    {records: [...]}} depending on the Evolution version."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    inner = data.get("messages")
    if isinstance(inner, list):
        return [r for r in inner if isinstance(r, dict)]
    if isinstance(inner, dict) and isinstance(inner.get("records"), list):
        return [r for r in inner["records"] if isinstance(r, dict)]
    return [data] if "key" in data else []


def parse_event(payload: Any) -> WebhookEvent:
    if not isinstance(payload, dict):
        return WebhookEvent("other")
    event = str(payload.get("event") or "").lower().replace("_", ".")
    data = payload.get("data")
    if event == "messages.upsert":
        return WebhookEvent("messages", records=records_of(data))
    if event == "connection.update" and isinstance(data, dict):
        reason = data.get("statusReason")
        return WebhookEvent("connection", state=str(data.get("state") or ""),
                            reason=reason if isinstance(reason, int) else None)
    return WebhookEvent("other")


def qr_data_uri(payload: Any) -> str:
    """The QR sits under `qrcode.base64` on create and flat `base64` on connect."""
    if not isinstance(payload, dict):
        return ""
    qr = payload.get("qrcode") if isinstance(payload.get("qrcode"), dict) else payload
    b64 = str(qr.get("base64") or "")
    if not b64:
        return ""
    return b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"


def connection_state(payload: Any) -> str:
    instance = payload.get("instance") if isinstance(payload, dict) else None
    state = instance.get("state") if isinstance(instance, dict) else None
    return str(state or "close")


def owner_phone(payload: Any) -> str:
    """+phone of the linked account from fetchInstances ("" when unknown)."""
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if not isinstance(row, dict):
            continue
        inst = row.get("instance") if isinstance(row.get("instance"), dict) else row
        owner = str(inst.get("ownerJid") or inst.get("owner") or "")
        digits = owner.partition("@")[0].split(":")[0]
        if digits.isdigit():
            return f"+{digits}"
        number = str(inst.get("number") or "")
        if number.isdigit():
            return f"+{number}"
    return ""
