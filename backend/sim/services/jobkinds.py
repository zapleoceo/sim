"""Job kinds and dedup keys shared by services (producers) and the worker (consumer)."""
from __future__ import annotations

REPLY = "reply"
SEND = "send"
FOLLOWUP = "followup"
CONTENT_PUBLISH = "content_publish"
MEDIA = "media"           # download a channel attachment (+ transcribe a voice message)


def reply_key(dialog_id: int) -> str:
    return f"reply:{dialog_id}"


def followup_key(dialog_id: int) -> str:
    return f"followup:{dialog_id}"


def media_key(dialog_id: int, message_id: int) -> str:
    return f"media:{dialog_id}:{message_id}"


def media_prefix(dialog_id: int) -> str:
    return f"media:{dialog_id}:"
