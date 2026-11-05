"""Denormalised dialog fields kept in step with messages (cheap inbox listing)."""
from __future__ import annotations

from sim.adapters.db.models import Dialog, Message

PREVIEW_LEN = 300


def apply_message(dialog: Dialog, message: Message) -> None:
    dialog.last_message_text = message.text[:PREVIEW_LEN]
    dialog.last_message_direction = message.direction
    dialog.last_message_at = message.created_at
