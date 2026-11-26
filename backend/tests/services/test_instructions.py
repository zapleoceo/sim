"""The fixed format rules shown to the reply model (sim/services/context/instructions.py)."""
from __future__ import annotations

from sim.domain.plan import ReplyPlan
from sim.services.context.instructions import FORMAT_RULES


def test_video_note_is_described_with_its_trigger_words() -> None:
    assert '{"type": "video_note"' in FORMAT_RULES
    for word in ("кружок", "кружочек", "видеосообщение"):
        assert f"«{word}»" in FORMAT_RULES


def test_media_is_never_announced() -> None:
    assert "никогда не пиши, что медиа делается" in FORMAT_RULES
    for phrase in ("«сейчас запишу»", "«скоро пришлю»", "прилетит через пару минут"):
        assert phrase in FORMAT_RULES
    assert "записывает кружок" in FORMAT_RULES          # the status is the only signal


def test_spoken_words_are_personal() -> None:
    assert "generate.speech" in FORMAT_RULES
    assert "«всем привет»" in FORMAT_RULES
    assert "10 секунд" in FORMAT_RULES


def test_memory_and_recall_are_explained() -> None:
    """docs/memory.md: today verbatim, the past by summaries, «А помнишь…?» once per turn."""
    for phrase in ("«Лента за сегодня»", "«Прошлые дни»", "«Заметках дня»", '"recall"',
                   "«в прошлую субботу»", "один раз за ход"):
        assert phrase in FORMAT_RULES, phrase


def test_the_plan_schema_knows_video_notes_and_speech() -> None:
    schema = str(ReplyPlan.model_json_schema())
    assert "video_note" in schema and "speech" in schema


def test_stickers_are_explained_only_from_her_list() -> None:
    """docs/stickers.md: one sticker now and then, only from «Стикеры»."""
    assert '{"type": "sticker", "sticker_id"' in FORMAT_RULES
    assert "«Стикеры»" in FORMAT_RULES and "[стикер 😂]" in FORMAT_RULES
    schema = str(ReplyPlan.model_json_schema())
    assert "sticker" in schema and "sticker_id" in schema
