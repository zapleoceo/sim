"""Owner prompt save and exact preview request boundary."""
from tests.conftest import decision_json
from tests.test_api_smoke import owner


async def test_authoritative_prompt_save_preserves_legacy_and_preview_uses_it(api, world, fake_llm):
    headers = await owner(api)
    url = f"/api/personas/{world.persona.id}"
    original = (await api.get(url)).json()
    before = (await api.get(url + "/prompt")).json()
    assert before["mode"] == "legacy"
    text = "  Only this behavioural text.\nDo not import previous style.  "
    response = await api.put(url + "/prompt", json={"text": text}, headers=headers)
    assert response.status_code == 200
    assert response.json()["agent_prompt"] == text
    after = (await api.get(url + "/prompt")).json()
    assert after["mode"] == "unified" and after["agent_prompt"] == text
    assert after["system"].startswith(text)
    assert after["technical"]["output_contract"]
    assert after["guard_system"]
    assert (await api.get(url)).json()["description"] == original["description"]
    # An older editor's full persona PUT must not clear the authoritative prompt.
    assert (await api.put(url, json=original, headers=headers)).status_code == 200
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
