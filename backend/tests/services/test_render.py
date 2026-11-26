from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sim.db.models import Call, ChatEvent, Generation, MediaAsset, Message
from sim.domain.enums import (
    Author,
    CallDirection,
    CallStatus,
    Direction,
    EventKind,
    GenerationKind,
    GenerationStatus,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    UnderstandingStatus,
)
from sim.services.context.render import (
    Related,
    Speakers,
    render_event,
    render_events,
    sticker_text,
)

AT = datetime(2026, 11, 21, 18, 30, tzinfo=UTC)      # 20:30 in Kyiv
WHO = Speakers(client="Дима", persona="Марина", tz="Europe/Kyiv")


def event(kind: EventKind, *, id: int = 1, data: dict[str, Any] | None = None,
          in_context: bool = True, **refs: Any) -> ChatEvent:
    return ChatEvent(id=id, chat_id=1, kind=kind, in_context=in_context, created_at=AT,
                     data=data or {}, actor="system", **refs)


def message(id: int, *, direction: Direction = Direction.IN, kind: MessageKind = MessageKind.TEXT,
            text: str = "", media_id: int | None = None,
            author: Author = Author.CLIENT) -> Message:
    return Message(id=id, chat_id=1, direction=direction, author=author, kind=kind, text=text,
                   media_id=media_id, status=MessageStatus.SENT)


def media(id: int, *, kind: MediaKind = MediaKind.PHOTO, description: str = "",
          understanding: UnderstandingStatus = UnderstandingStatus.DONE, caption: str = "",
          file_name: str = "", duration_s: float | None = None) -> MediaAsset:
    return MediaAsset(id=id, kind=kind, source=MediaSource.INCOMING, description=description,
                      understanding=understanding, caption=caption, file_name=file_name,
                      duration_s=duration_s, mime="x/y", storage_key=f"k{id}", tags=[])


def render_one(ev: ChatEvent, rel: Related) -> str | None:
    return render_event(ev, rel, WHO)


def test_incoming_text() -> None:
    rel = Related(messages={10: message(10, text="привет")})
    assert render_one(event(EventKind.MESSAGE_IN, message_id=10), rel) == \
        "[21.11 20:30] Дима: привет"


def test_incoming_photo_with_caption() -> None:
    rel = Related(messages={10: message(10, kind=MessageKind.PHOTO, text="смотри", media_id=5)},
                  media={5: media(5, description="кот на диване")})
    assert render_one(event(EventKind.MESSAGE_IN, message_id=10), rel) == \
        "[21.11 20:30] Дима: [фото] кот на диване; подпись: «смотри»"


def test_incoming_voice_file_and_failures() -> None:
    rel = Related(
        messages={10: message(10, kind=MessageKind.VOICE, media_id=5),
                  11: message(11, kind=MessageKind.FILE, media_id=6),
                  12: message(12, kind=MessageKind.PHOTO, media_id=7),
                  13: message(13, kind=MessageKind.PHOTO, media_id=8)},
        media={5: media(5, kind=MediaKind.AUDIO, description="как дела", duration_s=14),
               6: media(6, kind=MediaKind.FILE, description="договор аренды",
                        file_name="договор.pdf"),
               7: media(7, understanding=UnderstandingStatus.FAILED),
               8: media(8, understanding=UnderstandingStatus.PENDING)},
    )
    lines = [render_one(event(EventKind.MESSAGE_IN, id=i, message_id=m), rel)
             for i, m in enumerate((10, 11, 12, 13), start=1)]
    assert lines == [
        "[21.11 20:30] Дима: [голосовое 0:14] как дела",
        "[21.11 20:30] Дима: [файл договор.pdf] договор аренды",
        "[21.11 20:30] Дима: [фото, не удалось рассмотреть]",
        "[21.11 20:30] Дима: [фото, ещё не успела посмотреть]",
    ]


def test_outgoing_persona_operator_voice_and_photo() -> None:
    rel = Related(
        messages={20: message(20, direction=Direction.OUT, author=Author.PERSONA, text="хай"),
                  21: message(21, direction=Direction.OUT, author=Author.OPERATOR, text="я тут"),
                  22: message(22, direction=Direction.OUT, author=Author.PERSONA,
                              kind=MessageKind.VOICE, media_id=30),
                  23: message(23, direction=Direction.OUT, author=Author.PERSONA,
                              kind=MessageKind.PHOTO, media_id=31, text="это я")},
        media={30: media(30, kind=MediaKind.AUDIO, description="приветик", duration_s=2),
               31: media(31, caption="селфи у окна")},
    )
    lines = [render_one(event(EventKind.MESSAGE_OUT, message_id=m), rel)
             for m in (20, 21, 22, 23)]
    assert lines == [
        "[21.11 20:30] Марина: хай",
        "[21.11 20:30] Марина (писал оператор): я тут",
        "[21.11 20:30] Марина: [голосовое 0:02] «приветик»",
        "[21.11 20:30] Марина: [фото: селфи у окна]; подпись: «это я»",
    ]


def test_edited_deleted_note() -> None:
    rel = Related()
    assert render_one(event(EventKind.MESSAGE_EDITED, data={"old": "привт", "new": "привет"}),
                      rel) == "[21.11 20:30] Дима исправил сообщение: «привт» → «привет»"
    assert render_one(event(EventKind.MESSAGE_DELETED, data={"text": "ой", "direction": "in"}),
                      rel) == "[21.11 20:30] Дима удалил сообщение: «ой»"
    assert render_one(event(EventKind.OPERATOR_NOTE, data={"text": "он врач"}), rel) == \
        "[21.11 20:30] (заметка оператора: он врач)"


def test_send_failed() -> None:
    rel = Related(
        messages={20: message(20, direction=Direction.OUT, text="держи"),
                  21: message(21, direction=Direction.OUT, kind=MessageKind.PHOTO, media_id=31)},
        media={31: media(31, caption="селфи у окна")},
    )
    assert render_one(event(EventKind.SEND_FAILED, message_id=20), rel) == \
        "[21.11 20:30] (не доставлено: «держи»)"
    assert render_one(event(EventKind.SEND_FAILED, message_id=21), rel) == \
        "[21.11 20:30] (не доставлено: фото «селфи у окна»)"


def test_calls() -> None:
    ended = Call(id=7, chat_id=1, direction=CallDirection.IN, status=CallStatus.ENDED,
                 duration_s=192, transcript=[{"who": "client", "text": "алло", "t": 0},
                                             {"who": "persona", "text": "привет!", "t": 1}])
    missed = Call(id=8, chat_id=1, direction=CallDirection.IN, status=CallStatus.MISSED,
                  transcript=[])
    rel = Related(calls={7: ended, 8: missed})
    assert render_one(event(EventKind.CALL, call_id=7), rel) == (
        "[21.11 20:30] [звонок входящий, 3:12]\n  Дима: алло\n  Марина: привет!")
    assert render_one(event(EventKind.CALL, call_id=8), rel) == \
        "[21.11 20:30] [пропущенный звонок]"


def test_generation_lines() -> None:
    gen = Generation(id=3, persona_id=1, kind=GenerationKind.VIDEO, prompt="p",
                     caption="утро у окна", status=GenerationStatus.RUNNING)
    rel = Related(generations={3: gen})
    assert render_one(event(EventKind.GENERATION_STARTED, generation_id=3), rel) == \
        "[21.11 20:30] (готовлю видео: «утро у окна»)"
    assert render_one(event(EventKind.GENERATION_FAILED, generation_id=3), rel) == \
        "[21.11 20:30] (не получилось сделать видео «утро у окна»)"
    # Without the row the event data is enough.
    assert render_one(event(EventKind.GENERATION_FAILED,
                            data={"kind": "photo", "caption": "селфи"}), Related()) == \
        "[21.11 20:30] (не получилось сделать фото «селфи»)"


def test_attention_shows_only_the_missed_call_note() -> None:
    """Her attention decisions are for the operator; the reply agent reads only the note of
    a text after a missed call (docs/attention.md)."""
    note = event(EventKind.ATTENTION, data={
        "action": "text_after_missed_call", "reason": "освободилась",
        "note": "пропущенный звонок, ты была занята: сон"})
    assert render_one(note, Related()) == (
        "[21.11 20:30] (пропущенный звонок, ты была занята: сон)")
    plain = event(EventKind.ATTENTION, data={"action": "reply_later", "reason": "на рынке"})
    assert render_one(plain, Related()) is None
    assert render_one(event(EventKind.ATTENTION, data={"note": "x"}, in_context=False),
                      Related()) is None


def test_hidden_kinds_are_not_rendered() -> None:
    rel = Related(messages={10: message(10, text="привет")})
    hidden = [event(EventKind.GUARD_BLOCKED, data={"reason": "x"}, in_context=False),
              event(EventKind.MEDIA_UNDERSTOOD, in_context=False),
              event(EventKind.MEMORY_UPDATED, in_context=False),
              event(EventKind.AI_TOGGLED, in_context=False)]
    assert all(render_one(ev, rel) is None for ev in hidden)
    lines = render_events([*hidden, event(EventKind.MESSAGE_IN, id=9, message_id=10)], rel, WHO)
    assert [(line.event_id, line.text) for line in lines] == [(9, "[21.11 20:30] Дима: привет")]


def test_stickers_show_their_emoji_both_ways() -> None:
    """docs/stickers.md: «[стикер 😂]», plus what «Зрение» saw in a client's picture sticker."""
    rel = Related(
        messages={10: message(10, kind=MessageKind.STICKER, text="😂", media_id=5),
                  11: message(11, kind=MessageKind.STICKER, text="🙈", media_id=6),
                  12: message(12, kind=MessageKind.STICKER, media_id=7),
                  20: message(20, direction=Direction.OUT, author=Author.PERSONA,
                              kind=MessageKind.STICKER, text="🥰")},
        media={5: media(5, description="кот в очках"),
               6: media(6, description="старое", understanding=UnderstandingStatus.PENDING),
               7: media(7, understanding=UnderstandingStatus.SKIPPED)},
    )
    lines = [render_one(event(EventKind.MESSAGE_IN, id=i, message_id=m), rel)
             for i, m in enumerate((10, 11, 12), start=1)]
    assert lines == [
        "[21.11 20:30] Дима: [стикер 😂] кот в очках",
        "[21.11 20:30] Дима: [стикер 🙈]",
        "[21.11 20:30] Дима: [стикер]",
    ]
    assert render_one(event(EventKind.MESSAGE_OUT, message_id=20), rel) == \
        "[21.11 20:30] Марина: [стикер 🥰]"


def test_sticker_text_needs_no_picture() -> None:
    padded = message(1, kind=MessageKind.STICKER, text=" 😂 ")
    assert sticker_text(padded, None) == "[стикер 😂]"
    assert sticker_text(message(2, kind=MessageKind.STICKER), None) == "[стикер]"
