"""Unified prompt save, CSRF and validation."""
from sqlalchemy import select

from sim.adapters.db.models import Dialog, DialogEvent, Persona
from sim.adapters.db.session import transaction
from sim.services.reengagement import signature
from tests.conftest import OPERATOR, decision_json, login
from tests.test_api_smoke import owner
from tests.test_calls import chatting_world


async def test_prompt_save_preserves_fields_invalidates_plan_and_reaches_preview(api, container, world, fake_llm):
    did = await chatting_world(container, world)
    headers = await owner(api)
    url = f"/api/personas/{world.persona.id}"
    original = (await api.get(url)).json()
    before = (await api.get(url + "/prompt")).json()
    assert before["mode"] == "unified"
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, did)
        persona = await s.get(Persona, world.persona.id)
        old_signature = signature(dialog, persona)
    text = "  Synthetic unified instructions.\nKeep this spacing.  "
    response = await api.put(url + "/prompt", json={"text": text}, headers=headers)
    assert response.status_code == 200 and response.json()["agent_prompt"] == text
    after = (await api.get(url + "/prompt")).json()
    assert after["mode"] == "unified" and after["system"].startswith(text + "\n\n")
    assert [p["key"] for p in after["parts"]] == [
        "agent_prompt", "link_contract", "unified_boundaries", "output_contract"]
    assert (await api.get(url)).json()["description"] == original["description"]
    assert (await api.put(url, json={"name": original["name"], "description": original["description"],
                                   "strategy_id": original["strategy_id"]}, headers=headers)).status_code == 200
    assert (await api.get(url + "/prompt")).json()["agent_prompt"] == text
    fake_llm.replies = [decision_json(reply="Hello", stage="engaged")]
    assert (await api.post(url + "/preview", json={"message": "Hi"}, headers=headers)).status_code == 200
    assert fake_llm.gen_calls[-1][0][0]["content"] == after["system"]
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, did)
        persona = await s.get(Persona, world.persona.id)
        assert signature(dialog, persona) != old_signature
        events = list((await s.scalars(select(DialogEvent).where(DialogEvent.dialog_id == dialog.id))).all())
        assert any(e.kind == "persona" for e in events)
        assert all(text not in str(e.__dict__) for e in events)


async def test_prompt_write_is_owner_csrf_protected_and_rejects_blank(api, world):
    url = f"/api/personas/{world.persona.id}/prompt"
    assert (await api.put(url, json={"text": "x"})).status_code == 401
    await owner(api)
    assert (await api.put(url, json={"text": "x"})).status_code == 403
    token = await login(api, *OPERATOR)
    assert (await api.put(url, json={"text": "x"}, headers={"X-CSRF-Token": token})).status_code == 403
    headers = await owner(api)
    for value in ("", " \n ", "x" * 100001):
        assert (await api.put(url, json={"text": value}, headers=headers)).status_code == 422
    assert (await api.get(url)).json()["mode"] == "unified"
