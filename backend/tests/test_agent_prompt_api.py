"""Owner prompt save and exact preview request boundary."""
from sim.services.conversation import GUARD_RETRY_TEMPLATE, NEUTRAL_HANDOFF_REPLY
from sim.services.prompt import LINK_CONTRACT, OUTPUT_CONTRACT
from tests.conftest import OPERATOR, decision_json, login
from tests.test_api_smoke import owner


def rule(body: dict, key: str) -> str:
    return next(r["text"] for r in body["rules"] if r["key"] == key)


async def test_authoritative_prompt_save_preserves_legacy_and_preview_uses_it(api, world, fake_llm):
    headers = await owner(api)
    url = f"/api/personas/{world.persona.id}"
    original = (await api.get(url)).json()
    before = (await api.get(url + "/prompt")).json()
    assert before["mode"] == "legacy"
    # Legacy: the editor is offered the composed instructions (the system message without the
    # answer format), and the map still shows them piece by piece.
    assert before["agent_prompt"] == "\n\n".join(p["text"] for p in before["parts"][:-1])
    assert "Чего нельзя никогда" in before["agent_prompt"]
    assert "ЗАДАЧА НАПОМИНАНИЯ" in rule(before, "followup")
    text = "  Only this behavioural text.\nDo not import previous style.  "
    response = await api.put(url + "/prompt", json={"text": text}, headers=headers)
    assert response.status_code == 200
    assert response.json()["agent_prompt"] == text
    after = (await api.get(url + "/prompt")).json()
    assert after["mode"] == "unified" and after["agent_prompt"] == text
    # Unified: her text as typed, the link rule from the code and the answer format; nothing of
    # the description, the prohibitions or the strategy is mixed in.
    assert [(p["key"], p["source"], p["field"]) for p in after["parts"]] == [
        ("agent_prompt", "persona", "agent_prompt"), ("link_contract", "code", ""),
        ("output_contract", "setting", "output_contract")]
    assert after["system"] == "\n\n".join((text, LINK_CONTRACT, OUTPUT_CONTRACT))
    assert "Чего нельзя никогда" not in after["system"]
    assert rule(after, "guard_retry") == GUARD_RETRY_TEMPLATE
    assert rule(after, "neutral_handoff") == NEUTRAL_HANDOFF_REPLY
    assert rule(after, "followup").startswith("СОБЫТИЕ: плановое напоминание 1 из ")
    assert after["context"] and after["strategy_id"] == world.strategy.id
    assert (await api.get(url)).json()["description"] == original["description"]
    # An older editor's full persona PUT must not clear the authoritative prompt.
    legacy_body = {"name": original["name"], "description": original["description"],
                   "strategy_id": original["strategy_id"]}
    update = await api.put(url, json=legacy_body, headers=headers)
    assert update.status_code == 200, update.text
    assert (await api.get(url + "/prompt")).json()["mode"] == "unified"
    fake_llm.replies = [decision_json(reply="Hello", stage="engaged")]
    preview = await api.post(url + "/preview", json={"message": "Hi"}, headers=headers)
    assert preview.status_code == 200
    requests = preview.json()["requests"]
    assert requests[0]["messages"][0]["content"] == after["system"]
    assert any(m["content"] == "Hi" for m in requests[0]["messages"])
    assert any("guard" in r["workflow"] for r in requests)


async def test_prompt_write_requires_session_and_csrf(api, world):
    url = f"/api/personas/{world.persona.id}/prompt"
    assert (await api.put(url, json={"text": "not saved"})).status_code == 401
    await owner(api)
    assert (await api.put(url, json={"text": "not saved"})).status_code == 403
    assert (await api.get(url)).json()["mode"] == "legacy"


async def test_prompt_write_is_owner_only_and_keeps_blank_text_out(api, world):
    url = f"/api/personas/{world.persona.id}/prompt"
    operator = {"X-CSRF-Token": await login(api, *OPERATOR)}
    assert (await api.put(url, json={"text": "not saved"}, headers=operator)).status_code == 403
    assert (await api.get(url)).json()["mode"] == "legacy"       # an operator still reads the map
    headers = await owner(api)
    assert (await api.put(url, json={"text": ""}, headers=headers)).status_code == 422
    assert (await api.put(url, json={}, headers=headers)).status_code == 422
    assert (await api.get(url)).json()["mode"] == "legacy"
