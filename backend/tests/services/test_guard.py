from __future__ import annotations

from datetime import UTC, datetime

from sim.db.models import Persona
from sim.domain.plan import ReplyPlan
from sim.services.context.facts import FactsInput, compute_facts
from sim.services.guard import (
    UNREADABLE_REASON,
    guard_messages,
    outgoing_lines,
    parse_verdict,
)

PLAN = ReplyPlan.model_validate({"messages": [
    {"type": "text", "text": "привет"},
    {"type": "voice", "text": "как ты?"},
    {"type": "photo", "media_id": 3, "caption": "это я"},
    {"type": "video", "generate": {"prompt": "танец", "caption": "танцую дома", "tags": []}},
]})


def test_outgoing_lines_show_what_the_client_gets() -> None:
    assert outgoing_lines(PLAN) == [
        "1. [текст] привет",
        "2. [голосовое, вслух] как ты?",
        "3. [фото из медиатеки] подпись: это я",
        "4. [новое видео: танцую дома]",
    ]


def test_guard_prompt_has_facts_rules_and_client_lines() -> None:
    persona = Persona(name="Марина", language="ru", guard_rules="Не называть фамилию.")
    facts = compute_facts(FactsInput(now=datetime(2026, 11, 21, 18, 30, tzinfo=UTC),
                                     persona_tz="Europe/Kyiv"))
    system, user = guard_messages(persona, facts, ["[21.11 20:29] Дима: привет"], PLAN)
    assert system.role == "system" and "Марина" in (system.parts[0].text or "")
    text = user.parts[0].text or ""
    assert "Не называть фамилию." in text
    assert "20:30" in text
    assert "Дима: привет" in text
    assert "1. [текст] привет" in text


def test_parse_verdict() -> None:
    assert parse_verdict('{"ok": true, "reason": ""}').ok
    blocked = parse_verdict('```json\n{"ok": false, "reason": "признался, что бот"}\n```')
    assert not blocked.ok and blocked.reason == "признался, что бот"
    unreadable = parse_verdict("всё хорошо")
    assert not unreadable.ok and unreadable.reason == UNREADABLE_REASON


def test_video_notes_are_shown_to_the_guard() -> None:
    plan = ReplyPlan.model_validate({"messages": [
        {"type": "video_note", "media_id": 5},
        {"type": "video_note", "generate": {"prompt": "машу рукой", "caption": "привет",
                                            "speech": "Привет, Дима!"}},
    ]})
    assert outgoing_lines(plan) == ["1. [кружок из медиатеки]", "2. [новый кружок: привет]"]


def test_guard_blocks_announcing_that_media_is_being_made() -> None:
    persona = Persona(name="Марина", language="ru", guard_rules="")
    facts = compute_facts(FactsInput(now=datetime(2026, 11, 21, 18, 30, tzinfo=UTC),
                                     persona_tz="Europe/Kyiv"))
    system = guard_messages(persona, facts, [], PLAN)[0].parts[0].text or ""
    assert "5. Персонаж не объявляет" in system
    assert "сейчас запишу" in system and "придёт позже" in system


def test_owner_guard_prompt_replaces_the_default() -> None:
    persona = Persona(name="Марина", language="uk", guard_rules="",
                      agents={"guard": {"prompt": 'Проверь ответ {name} ({language}). {"ok"}'}})
    facts = compute_facts(FactsInput(now=datetime(2026, 11, 21, 18, 30, tzinfo=UTC),
                                     persona_tz="Europe/Kyiv"))
    system = guard_messages(persona, facts, [], PLAN)[0].parts[0].text
    assert system == 'Проверь ответ Марина (uk). {"ok"}'
