"""What the client sees in Telegram while the persona is busy: «печатает», «записывает голосовое»,
«записывает кружок», «отправляет фото / видео» (docs/telegram.md, «Статусы»).

One mapping for everyone who shows a status: the reply turn (thinking), the plan executor and
the generation (slow work before a message exists) and the send task (the short action right
before a message goes out). Plan action types, message kinds and generation kinds share the
names `text`, `voice`, `photo`, `video`, `video_note`, so one key works for all three."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ChatAction(StrEnum):
    TYPING = "typing"
    RECORD_AUDIO = "record_audio"
    RECORD_ROUND = "record_round"
    UPLOAD_PHOTO = "upload_photo"
    UPLOAD_VIDEO = "upload_video"
    UPLOAD_DOCUMENT = "upload_document"
    CHOOSE_STICKER = "choose_sticker"
    CANCEL = "cancel"


ACTION_BY_KIND: Mapping[str, ChatAction] = {
    "text": ChatAction.TYPING,
    "voice": ChatAction.RECORD_AUDIO,
    "video_note": ChatAction.RECORD_ROUND,
    "photo": ChatAction.UPLOAD_PHOTO,
    "video": ChatAction.UPLOAD_VIDEO,
    "sticker": ChatAction.CHOOSE_STICKER,
}
# How long the slow work before a message of this kind may take (TTS, generation), seconds.
SLOW_WORK_S: Mapping[str, float] = {"voice": 30.0, "photo": 120.0, "video": 400.0,
                                    "video_note": 400.0}
THINKING_S = 40.0               # the reply model writes its plan
MAX_PRESENCE_S = 300.0          # longest status one request may ask for


@dataclass(frozen=True)
class Busy:
    action: ChatAction
    seconds: float


THINKING = Busy(ChatAction.TYPING, THINKING_S)


def chat_action_for(kind: str) -> ChatAction:
    """The action shown while a message of this kind is being made or sent."""
    return ACTION_BY_KIND.get(kind, ChatAction.UPLOAD_DOCUMENT)


def busy_with(kind: str) -> Busy:
    """The status for the slow part of a `kind` message (TTS, a generation)."""
    return Busy(chat_action_for(kind), SLOW_WORK_S.get(kind, THINKING_S))


def ended_by(current: str, sent_kind: str) -> bool:
    """Whether a delivered message of `sent_kind` ends the status `current`. Thinking ends with
    any message; slow work only with its own result (a text sent while a video is being made
    does not stop «отправляет видео»)."""
    return current in (ChatAction.TYPING, chat_action_for(sent_kind))
