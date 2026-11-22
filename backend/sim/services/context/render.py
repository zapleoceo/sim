"""How each chat event looks to the agent (docs/context.md §1). Pure: the builder loads the
rows the events point to (`Related`) and this module only formats them, in Russian, as
`[21.11 20:30] Имя: текст`. Kinds the agent must not see render to None."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from sim.db.models import Call, ChatEvent, Generation, MediaAsset, Message
from sim.domain.enums import (
    Author,
    CallDirection,
    CallStatus,
    EventKind,
    GenerationKind,
    MediaKind,
    MessageKind,
    UnderstandingStatus,
)
from sim.domain.initiative import TOPIC_KEY, initiative_line
from sim.services.context.facts import clock_duration, stamp

MEDIA_LABELS = {
    MessageKind.PHOTO: "фото",
    MessageKind.VIDEO: "видео",
    MessageKind.VOICE: "голосовое",
    MessageKind.VIDEO_NOTE: "кружок",
    MessageKind.FILE: "файл",
    MessageKind.STICKER: "стикер",
}
TIMED_KINDS = (MessageKind.VOICE, MessageKind.VIDEO, MessageKind.VIDEO_NOTE)
NOT_UNDERSTOOD = {
    MediaKind.PHOTO: "не удалось рассмотреть",
    MediaKind.VIDEO: "не удалось рассмотреть",
    MediaKind.AUDIO: "не удалось расслышать",
    MediaKind.FILE: "не удалось прочитать",
}
GENERATION_LABELS = {GenerationKind.PHOTO: "фото", GenerationKind.VIDEO: "видео",
                     GenerationKind.VIDEO_NOTE: "кружок",
                     GenerationKind.VOICE: "голосовое"}
CALL_DIRECTIONS = {CallDirection.IN: "входящий", CallDirection.OUT: "исходящий"}


@dataclass(frozen=True)
class Speakers:
    client: str
    persona: str
    tz: str


@dataclass
class Related:
    """Rows referenced by the events being rendered, by id."""

    messages: dict[int, Message] = field(default_factory=dict)
    media: dict[int, MediaAsset] = field(default_factory=dict)
    calls: dict[int, Call] = field(default_factory=dict)
    generations: dict[int, Generation] = field(default_factory=dict)


@dataclass(frozen=True)
class TimelineLine:
    event_id: int
    kind: EventKind
    text: str


def media_label(kind: MessageKind, media: MediaAsset | None) -> str:
    label = MEDIA_LABELS.get(kind, "вложение")
    if media is None:
        return label
    if kind == MessageKind.FILE and media.kind == MediaKind.AUDIO:
        label = "аудио"
    if kind in TIMED_KINDS or label == "аудио":
        if media.duration_s:
            label = f"{label} {clock_duration(media.duration_s)}"
    elif kind == MessageKind.FILE and media.file_name:
        label = f"{label} {media.file_name}"
    return label


def incoming_media(kind: MessageKind, media: MediaAsset | None) -> str:
    label = media_label(kind, media)
    if media is None:
        return f"[{label}]"
    if media.understanding == UnderstandingStatus.DONE and media.description:
        return f"[{label}] {one_line(media.description)}"
    if media.understanding == UnderstandingStatus.FAILED:
        return f"[{label}, {NOT_UNDERSTOOD.get(media.kind, 'не удалось разобрать')}]"
    if media.understanding == UnderstandingStatus.PENDING:
        return f"[{label}, ещё не успела посмотреть]"
    return f"[{label}]"


def outgoing_media(kind: MessageKind, media: MediaAsset | None) -> str:
    label = media_label(kind, media)
    if media is None:
        return f"[{label}]"
    if kind == MessageKind.VOICE and media.description:
        return f"[{label}] «{one_line(media.description)}»"
    if media.caption:
        return f"[{label}: {one_line(media.caption)}]"
    return f"[{label}]"


def one_line(text: str) -> str:
    return " ".join(text.split())


def _message_body(message: Message, media_text: str | None) -> str:
    text = one_line(message.text)
    if media_text is None:
        return text
    return f"{media_text}; подпись: «{text}»" if text else media_text


def _has_media(message: Message) -> bool:
    return message.kind != MessageKind.TEXT or message.media_id is not None


def _render_message(event: ChatEvent, data: dict[str, Any], rel: Related,
                    who: Speakers) -> str | None:
    message = rel.messages.get(event.message_id) if event.message_id else None
    if message is None:
        return None
    media = rel.media.get(message.media_id) if message.media_id else None
    if event.kind == EventKind.MESSAGE_IN:
        media_text = incoming_media(message.kind, media) if _has_media(message) else None
        return f"{who.client}: {_message_body(message, media_text)}"
    media_text = outgoing_media(message.kind, media) if _has_media(message) else None
    speaker = who.persona
    if message.author == Author.OPERATOR:
        speaker = f"{who.persona} (писал оператор)"
    return f"{speaker}: {_message_body(message, media_text)}"


def _render_edited(event: ChatEvent, data: dict[str, Any], rel: Related,
                   who: Speakers) -> str | None:
    old = one_line(str(data.get("old", "")))
    new = one_line(str(data.get("new", "")))
    return f"{who.client} исправил сообщение: «{old}» → «{new}»"


def _render_deleted(event: ChatEvent, data: dict[str, Any], rel: Related,
                    who: Speakers) -> str | None:
    text = one_line(str(data.get("text", ""))) or "вложение"
    if data.get("direction") == "out":
        return f"{who.client} удалил сообщение {who.persona}: «{text}»"
    return f"{who.client} удалил сообщение: «{text}»"


def _render_send_failed(event: ChatEvent, data: dict[str, Any], rel: Related,
                        who: Speakers) -> str | None:
    message = rel.messages.get(event.message_id) if event.message_id else None
    media: MediaAsset | None = None
    if message is not None:
        kind, text = message.kind, one_line(message.text)
        media = rel.media.get(message.media_id) if message.media_id else None
    else:
        kind = MessageKind(data.get("kind", MessageKind.TEXT))
        text = one_line(str(data.get("text", "")))
    if kind == MessageKind.TEXT:
        return f"(не доставлено: «{text}»)"
    detail = one_line(media.caption or media.description) if media is not None else text
    label = MEDIA_LABELS.get(kind, "вложение")
    if detail:
        return f"(не доставлено: {label} «{detail}»)"
    return f"(не доставлено: {label})"


def _render_call(event: ChatEvent, data: dict[str, Any], rel: Related,
                 who: Speakers) -> str | None:
    call = rel.calls.get(event.call_id) if event.call_id else None
    if call is None:
        return None
    direction = CALL_DIRECTIONS.get(call.direction, "")
    if call.status == CallStatus.MISSED:
        if call.direction == CallDirection.IN:
            return "[пропущенный звонок]"
        return "[звонок без ответа]"
    if call.status == CallStatus.DECLINED:
        return f"[звонок {direction}, отклонён]"
    if call.status == CallStatus.FAILED:
        return f"[звонок {direction}, сорвался]"
    lines = [f"[звонок {direction}, {clock_duration(call.duration_s)}]"]
    for turn in call.transcript or []:
        speaker = who.client if turn.get("who") == "client" else who.persona
        text = one_line(str(turn.get("text", "")))
        if text:
            lines.append(f"  {speaker}: {text}")
    return "\n".join(lines)


def _generation_words(event: ChatEvent, data: dict[str, Any], rel: Related) -> tuple[str, str]:
    generation = rel.generations.get(event.generation_id) if event.generation_id else None
    if generation is not None:
        return GENERATION_LABELS.get(generation.kind, "медиа"), one_line(generation.caption)
    kind = GenerationKind(data.get("kind", GenerationKind.PHOTO))
    return GENERATION_LABELS.get(kind, "медиа"), one_line(str(data.get("caption", "")))


def _render_generation_started(event: ChatEvent, data: dict[str, Any], rel: Related,
                               who: Speakers) -> str | None:
    label, caption = _generation_words(event, data, rel)
    return f"(готовлю {label}: «{caption}»)"


def _render_generation_failed(event: ChatEvent, data: dict[str, Any], rel: Related,
                              who: Speakers) -> str | None:
    label, caption = _generation_words(event, data, rel)
    return f"(не получилось сделать {label} «{caption}»)"


def _render_note(event: ChatEvent, data: dict[str, Any], rel: Related,
                 who: Speakers) -> str | None:
    return f"(заметка оператора: {one_line(str(data.get('text', '')))})"


def _render_attention(event: ChatEvent, data: dict[str, Any], rel: Related,
                      who: Speakers) -> str | None:
    """Only the note for the reply (a call she missed because she was busy); her other
    attention decisions are for the operator."""
    note = one_line(str(data.get("note", "")))
    return f"({note})" if note else None


def _render_initiative(event: ChatEvent, data: dict[str, Any], rel: Related,
                       who: Speakers) -> str | None:
    """A planned message that came due (docs/initiative.md): she writes first."""
    topic = one_line(str(data.get(TOPIC_KEY, "")))
    return initiative_line(topic) if topic else None


Renderer = Callable[[ChatEvent, dict[str, Any], Related, Speakers], str | None]

# One renderer per visible kind; a kind missing here is not shown to the agent.
RENDERERS: dict[EventKind, Renderer] = {
    EventKind.MESSAGE_IN: _render_message,
    EventKind.MESSAGE_OUT: _render_message,
    EventKind.MESSAGE_EDITED: _render_edited,
    EventKind.MESSAGE_DELETED: _render_deleted,
    EventKind.SEND_FAILED: _render_send_failed,
    EventKind.CALL: _render_call,
    EventKind.GENERATION_STARTED: _render_generation_started,
    EventKind.GENERATION_FAILED: _render_generation_failed,
    EventKind.OPERATOR_NOTE: _render_note,
    EventKind.ATTENTION: _render_attention,
    EventKind.INITIATIVE: _render_initiative,
}


def render_event(event: ChatEvent, rel: Related, who: Speakers) -> str | None:
    """One timeline entry with its time prefix, or None if the agent must not see it."""
    renderer = RENDERERS.get(event.kind)
    if renderer is None or event.in_context is False:
        return None
    body = renderer(event, event.data or {}, rel, who)
    if body is None:
        return None
    return f"[{stamp(event.created_at, who.tz)}] {body}"


def render_events(events: Iterable[ChatEvent], rel: Related, who: Speakers) -> list[TimelineLine]:
    lines = []
    for event in events:
        text = render_event(event, rel, who)
        if text is not None:
            lines.append(TimelineLine(event_id=event.id, kind=event.kind, text=text))
    return lines
