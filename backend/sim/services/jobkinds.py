"""Job kinds and dedup keys shared by services (producers) and the worker (consumer)."""
from __future__ import annotations

REPLY = "reply"
SEND = "send"
FOLLOWUP = "followup"
CONTENT_PUBLISH = "content_publish"


def reply_key(dialog_id: int) -> str:
    return f"reply:{dialog_id}"


def followup_key(dialog_id: int) -> str:
    return f"followup:{dialog_id}"
