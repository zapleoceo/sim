"""The persona's single prompt is a field of the persona: saved with it, validated, journaled."""
from sqlalchemy import select

from sim.adapters.db.models import Dialog, DialogEvent, Persona
from sim.adapters.db.session import transaction
from sim.services.reengagement import signature
from tests.conftest import OPERATOR, decision_json, login
from tests.test_api_smoke import owner
from tests.test_calls import chatting_world


async def test_prompt_saved_with_the_persona_reaches_preview_and_invalidates_plan(api, container, world, fake_llm):
    did = await chatting_world(container, world)
    headers = await owner(api)
    url = f"/api/personas/{world.persona.id}"
    original = (await api.get(url)).json()
    assert original["agent_prompt"].startswith("Ты тестовая персона.")
    assert not {"age", "description", "prohibitions"} & set(original)
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, did)
        persona = await s.get(Persona, world.persona.id)
        old_signature = signature(dialog, persona)
    text = "Synthetic unified instructions.\nKeep this line."
    response = await api.put(url, json={**original, "gender": "female", "agent_prompt": text}, headers=headers)
    assert response.status_code == 200 and response.json()["agent_prompt"] == text
    assert (await api.get(url)).json()["agent_prompt"] == text
    fake_llm.replies = [decision_json(reply="Hello", stage="engaged")]
    assert (await api.post(url + "/preview", json={"message": "Hi"}, headers=headers)).status_code == 200
    assert fake_llm.gen_calls[-1][0][0]["content"].startswith(text + "\n\n")
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, did)
        persona = await s.get(Persona, world.persona.id)
        assert signature(dialog, persona) != old_signature
        events = list((await s.scalars(select(DialogEvent).where(DialogEvent.dialog_id == dialog.id))).all())
        assert any(e.kind == "persona" and "промпт агента" in e.to_value for e in events)
        assert all(text not in str(e.__dict__) for e in events)


async def test_prompt_is_required_owner_only_and_has_no_separate_endpoint(api, world):
    url = f"/api/personas/{world.persona.id}"
    body = {"name": "Марина", "agent_prompt": "x"}
    assert (await api.put(url, json=body)).status_code == 401
    token = await login(api, *OPERATOR)
    assert (await api.put(url, json=body, headers={"X-CSRF-Token": token})).status_code == 403
    headers = await owner(api)
    for value in ("", " \n ", "x" * 100001):
        assert (await api.put(url, json={**body, "agent_prompt": value}, headers=headers)).status_code == 422
    assert (await api.put(url, json={"name": "Марина"}, headers=headers)).status_code == 422
    for path in ("/prompt", "/context"):
        assert (await api.get(url + path)).status_code in (404, 405)
