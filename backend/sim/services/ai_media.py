"""What every AI-generated photo and video note of a persona shares: the label, the names of the
stored files, the outbound message and how such a message looks in the conversation history.

Generated files are content-addressed (the name is a hash of the request), so one request is
generated once and reused. The prefix of the name is what the send gates key on
(`OutboundService`): a generated photo always goes out with the label as its caption; a
generated video note goes only to the allowlisted account, after the label."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sim.adapters.db.models import Dialog, Message
from sim.domain.enums import Author, Delivery, Direction

AI_LABEL = "Сгенерировано ИИ"
MEDIA_URL = "/api/media/"
AI_VIDEO_PREFIX = "aivn_"            # generated video notes
AI_PHOTO_PREFIX = "aiph_"            # generated photos
HASH_CHARS = 40
# The rule as the admin shows it on the persona page (read-only; OutboundService enforces it).
AI_PHOTO_RULE = (
    f"Каждое сгенерированное фото уходит с подписью «{AI_LABEL}». Подпись ставит код при "
    "отправке, что бы ни было написано в самом сообщении."
)


def stored_name(prefix: str, key: str, suffix: str) -> str:
    return f"{prefix}{key[:HASH_CHARS]}.{suffix}"


def is_ai_video(media_url: str | None) -> bool:
    return (media_url or "").startswith(MEDIA_URL + AI_VIDEO_PREFIX)


def is_ai_photo(media_url: str | None) -> bool:
    return (media_url or "").startswith(MEDIA_URL + AI_PHOTO_PREFIX)


def ai_message(dialog: Dialog, text: str, now: datetime, **fields: Any) -> Message:
    """A queued outbound message written by the system on the persona's behalf."""
    return Message(dialog_id=dialog.id, direction=Direction.OUT, author=Author.AI, text=text,
                   delivery=Delivery.QUEUED, created_at=now, **fields)


def failed_message(dialog: Dialog, text: str, error: str, now: datetime) -> Message:
    """Why nothing came: a failed message in the dialog, visible in the admin and never sent
    anywhere (it has no SEND job)."""
    message = ai_message(dialog, text, now)
    message.delivery, message.error = Delivery.FAILED, error[:500]
    return message


def history_text(message: Message) -> str:
    """How a message reads in the history the models get. A generated photo or note is a short
    marker with the scene the persona asked for (kept in `original_text`); the label is written
    by code, not said by her, so alone it is nothing ("" is dropped from the history)."""
    scene = f": {message.original_text}" if message.original_text else ""
    if is_ai_photo(message.media_url):
        return f"[ты отправила своё фото{scene}]"
    if is_ai_video(message.media_url):
        return f"[ты отправила свой кружочек{scene}]"
    if message.author is Author.AI and message.text == AI_LABEL:
        return ""
    return message.text
