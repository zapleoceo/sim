"""Job kinds and dedup keys shared by services (producers) and the worker (consumer)."""
from __future__ import annotations

REPLY = "reply"
SEND = "send"
FOLLOWUP = "followup"
CONTENT_PUBLISH = "content_publish"
SIM_REPLY = "sim_reply"   # a persona answer inside an admin simulation branch
MEDIA = "media"           # download a channel attachment (+ transcribe a voice message)
PASSPORT = "passport"     # the passportist agent updates the lead passport after a reply


def reply_key(dialog_id: int) -> str:
    return f"reply:{dialog_id}"


def followup_key(dialog_id: int) -> str:
    return f"followup:{dialog_id}"


def media_key(dialog_id: int, message_id: int) -> str:
    return f"media:{dialog_id}:{message_id}"


def media_prefix(dialog_id: int) -> str:
    return f"media:{dialog_id}:"


def sim_reply_key(branch_id: int, message_id: int) -> str:
    return f"sim_reply:{branch_id}:{message_id}"


def sim_reply_prefix(branch_id: int) -> str:
    return f"sim_reply:{branch_id}:"


def passport_key(dialog_id: int) -> str:
    return f"passport:{dialog_id}"
