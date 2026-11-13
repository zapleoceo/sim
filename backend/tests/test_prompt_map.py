"""The persona prompt map of the admin: every piece of the prompt with its source."""
from __future__ import annotations

from datetime import date, datetime

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.services.call_prompt import call_identity
from sim.services.prompt import OUTPUT_CONTRACT, StrategyProfile
from sim.services.prompt_map import EXAMPLE_REF, example_context, fixed_rules
from sim.services.video_note import AI_LABEL, AI_VIDEO_RULE
from tests.conftest import OPERATOR, login


def test_example_context_is_the_dynamic_message_with_her_facts_and_placeholder_client():
    text = example_context(datetime(2026, 11, 14, 9, 0), "- Тебе 45", StrategyProfile())
    assert text.startswith("ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС")
    assert "- Тебе 45" in text and "- Текущий этап: first_contact" in text
    assert "- Канал: telegram" in text and "2026-11-14 09:00" in text
    with_link = example_context(datetime(2026, 11, 14), "", StrategyProfile(registration_url="https://x/?r={ref}"))
    assert f"https://x/?r={EXAMPLE_REF}" in with_link and "ТВОИ ТОЧНЫЕ ДАННЫЕ" not in with_link


def test_fixed_rules_are_the_call_identity_and_the_video_note_label():
    call, note = fixed_rules("Марина")
    assert (call.key, note.key) == ("call", "video_note")
    assert call_identity("Марина") in call.text and "ИИ-ассистент" in call.text
    assert note.text == AI_VIDEO_RULE and AI_LABEL in note.text and "Кружочки ИИ" in note.text


async def test_api_returns_the_prompt_piece_by_piece(api, world, container):
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.birth_date, persona.routine = date(1981, 3, 2), "ежедневно 00:00-23:59 дома"
    await login(api, OPERATOR[0])
    body = (await api.get(f"/api/personas/{world.persona.id}/prompt")).json()
    parts = body["parts"]
    assert body["system"] == "\n\n".join(p["text"] for p in parts) and body["fixed"] == ""
    assert body["strategy_id"] == world.strategy.id
    by_key = {p["key"]: p for p in parts}
    assert by_key["prohibitions"]["text"] == "Чего нельзя никогда:\n- не обещать доход\n- не просить пароли"
    assert (by_key["prohibitions"]["source"], by_key["prohibitions"]["field"]) == ("persona", "prohibitions")
    assert "ежедневно 00:00-23:59 дома" in by_key["routine"]["text"]
    assert by_key["strategy"]["source"] == "strategy"
    assert parts[-1] == {"key": "output_contract", "title": "Формат ответа персонажа",
                         "text": OUTPUT_CONTRACT, "source": "setting", "field": "output_contract"}
    assert "родилась 2 марта 1981" in body["context"] and "- Где ты сейчас по распорядку: дома" in body["context"]
    assert [r["key"] for r in body["rules"]] == ["call", "video_note"]
    assert all(r["title"] and r["text"] for r in body["rules"])
