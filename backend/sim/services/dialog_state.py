"""Denormalised dialog fields kept in step with messages (cheap inbox listing)."""
from __future__ import annotations

from sim.adapters.db.models import Dialog, Message

PREVIEW_LEN = 300
# A message the client edited is stored again with this mark: a correction, not a new turn.
EDIT_PREFIX = "[изменено] "


def turns(messages: list[Message]) -> list[Message]:
    """Client messages that are real turns (edits of earlier messages left out)."""
    return [m for m in messages if not m.text.startswith(EDIT_PREFIX)]


def apply_message(dialog: Dialog, message: Message) -> None:
    dialog.last_message_text = message.text[:PREVIEW_LEN]
    dialog.last_message_direction = message.direction
    dialog.last_message_at = message.created_at
