"""Pure parsing of Evolution API v2 webhook payloads. No I/O."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sim.ports.channel import InboundMessage

WA_MEDIA_PREFIX = "wa-msg:"

# (key, subfield) whose string a human actually reads.
_TEXT_KEYS: tuple[tuple[str, str], ...] = (
    ("conversation", ""),
    ("extendedTextMessage", "text"),
    ("imageMessage", "caption"),
    ("videoMessage", "caption"),
    ("documentMessage", "caption"),
    ("buttonsResponseMessage", "selectedDisplayText"),
    ("listResponseMessage", "title"),
    ("templateButtonReplyMessage", "selectedDisplayText"),
)
_MEDIA_KINDS: tuple[tuple[str, str], ...] = (
    ("imageMessage", "image"),
    ("stickerMessage", "image"),
    ("videoMessage", "video"),
    ("audioMessage", "voice"),
)


def unwrap(message: Any) -> dict[str, Any]:
    """Peel the envelopes (disappearing / view-once / caption doc) around a message."""
    body = message if isinstance(message, dict) else {}
    for _ in range(3):
        for envelope in (
            "ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
            "documentWithCaptionMessage",
        ):
            inner = body.get(envelope)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                body = inner["message"]
                break
        else:
            return body
    return body


def text_of(message: Any) -> str:
    body = unwrap(message)
    for key, sub in _TEXT_KEYS:
        v = body.get(key)
        if not sub and isinstance(v, str):
            return v
        if sub and isinstance(v, dict) and isinstance(v.get(sub), str):
            return v[sub]
    return ""


def media_kind_of(message: Any) -> str | None:
    body = unwrap(message)
    for key, kind in _MEDIA_KINDS:
        if isinstance(body.get(key), dict):
            return kind
    return None


def phone_of(jid: str) -> str | None:
    """+<digits> from a plain WhatsApp address; None for @lid / groups / anything else."""
    local, _, domain = str(jid or "").partition("@")
    if domain != "s.whatsapp.net":
        return None
    digits = local.split(":")[0]
    return f"+{digits}" if digits.isdigit() else None


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC).replace(tzinfo=None)


def message_from_record(raw: Any) -> InboundMessage | None:
    """One Evolution message record to InboundMessage; None when it must be skipped."""
    if not isinstance(raw, dict):
        return None
    key = raw.get("key") or {}
    jid = str(key.get("remoteJid") or "")
    if not jid or jid.endswith("@g.us") or jid == "status@broadcast" or "@broadcast" in jid:
        return None
    message = raw.get("message")
    kind = media_kind_of(message)
    text = text_of(message)
    if not text and not kind:
        return None
    from_me = bool(key.get("fromMe"))
    mid = key.get("id") or None
    return InboundMessage(
        external_thread_id=jid,
        external_user_id=jid,
        text=text,
        occurred_at=_ts(raw.get("messageTimestamp")),
        external_id=str(mid) if mid else None,
        user_name=None if from_me else (raw.get("pushName") or None),
        phone=phone_of(str(key.get("remoteJidAlt") or "")) or phone_of(jid),
        media_kind=kind,
        media_url=f"{WA_MEDIA_PREFIX}{mid}" if kind and mid else None,
        from_me=from_me,
    )


def parse_evolution_webhook(payload: Any) -> list[InboundMessage]:
    """messages.upsert payload (data is one record or a list) to InboundMessage list."""
    if not isinstance(payload, dict):
        return []
    event = str(payload.get("event") or "").lower().replace("_", ".")
    if event != "messages.upsert":
        return []
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        records = data["messages"]
    elif isinstance(data, list):
        records = data
    else:
        records = [data]
    out = [m for r in records if (m := message_from_record(r)) is not None]
    return out


def qr_data_uri(payload: Any) -> str:
    """QR sits under `qrcode.base64` on create and flat `base64` on connect."""
    if not isinstance(payload, dict):
        return ""
    qr = payload.get("qrcode")
    if not isinstance(qr, dict):
        qr = payload
    b64 = qr.get("base64") or ""
    if not b64:
        return ""
    return b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"
