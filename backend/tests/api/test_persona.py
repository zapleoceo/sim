"""Persona page: persona read/update with model checks, models, voices, instructions, channels."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona
from sim.ports import ModelInfo
from sim.services.context.instructions import FORMAT_RULES
from sim.services.voices import GEMINI_VOICES as VOICES
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp


def model(model_id: str, *, available: bool = True) -> ModelInfo:
    return ModelInfo(id=model_id, available=available, input_modalities=("text", "image"),
                     output_modalities=("text",), capabilities=("chat", "json"))


async def test_get_persona(api: ApiClient, world: World) -> None:
    response = await api.get(pp(world))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == world.persona_id
    assert body["name"] == "Марина"
    assert body["model_reply"] == "google/reply"
    assert body["reply_delay_s"] == 8
    assert "created_at" not in body and "updated_at" not in body


async def test_persona_404_when_unknown(api: ApiClient) -> None:
    response = await api.get("/api/personas/999999")
    assert response.status_code == 404
    assert "error" in response.json()
    assert (await api.get("/api/personas")).json() == {"items": []}


async def test_update_persona_partial(api: ApiClient, world: World, db: AsyncSession) -> None:
    response = await api.put(pp(world), json={
        "prompt": "Новый промт", "birth_date": "2001-05-17", "timezone": "Europe/Kyiv",
        "photo_enabled": False, "id": 12345, "created_at": "ignored"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["prompt"], body["birth_date"], body["photo_enabled"]) == (
        "Новый промт", "2001-05-17", False)
    assert body["name"] == "Марина"
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None and persona.prompt == "Новый промт"


async def test_update_persona_rejects_bad_values(api: ApiClient, world: World) -> None:
    for payload in ({"timezone": "Mars/Base"}, {"reply_delay_s": -1}, {"name": ""},
                    {"avatar_media_id": 999999}):
        response = await api.put(pp(world), json=payload)
        assert response.status_code == 400, payload
        assert "error" in response.json()


async def test_model_ids_are_checked_against_a_readable_catalog(api: ApiClient, world: World,
                                                                deps: FakeDeps) -> None:
    deps.llm.models = [model("google/reply"), model("google/guard", available=False)]
    unknown = await api.put(pp(world), json={"model_guard": "nobody/model"})
    assert unknown.status_code == 400
    assert "nobody/model" in unknown.json()["error"]
    # A listed model saves even when the catalog says it is unavailable right now.
    unavailable = await api.put(pp(world), json={"model_guard": "google/guard"})
    assert unavailable.status_code == 200
    assert unavailable.json()["model_guard"] == "google/guard"
    # Clearing a model is always allowed.
    assert (await api.put(pp(world), json={"model_guard": ""})).status_code == 200


async def test_unchanged_model_missing_from_catalog_does_not_block(api: ApiClient, world: World,
                                                                   deps: FakeDeps) -> None:
    deps.llm.models = [model("google/other")]
    response = await api.put(pp(world), json={"model_reply": "google/reply",
                                                   "prompt": "ещё"})
    assert response.status_code == 200


async def test_models_saved_unchecked_when_catalog_is_empty_or_down(
        api: ApiClient, world: World, deps: FakeDeps) -> None:
    assert (await api.put(pp(world), json={"model_tts": "a/tts"})).status_code == 200
    deps.llm.fail = True
    response = await api.put(pp(world), json={"model_image": "b/image"})
    assert response.status_code == 200
    assert response.json()["model_image"] == "b/image"


async def test_models_list(api: ApiClient, deps: FakeDeps) -> None:
    deps.llm.models = [model("google/reply", available=False)]
    response = await api.get("/api/models")
    assert response.status_code == 200
    assert response.json() == {"items": [{
        "id": "google/reply", "available": False, "input_modalities": ["text", "image"],
        "output_modalities": ["text"], "capabilities": ["chat", "json"]}]}


async def test_models_list_when_broker_is_down_is_502(api: ApiClient, deps: FakeDeps) -> None:
    deps.llm.fail = True
    response = await api.get("/api/models")
    assert response.status_code == 502
    assert response.json() == {"error": "Broker недоступен"}


async def test_voices_and_instructions(api: ApiClient) -> None:
    voices = await api.get("/api/voices")
    assert voices.status_code == 200
    assert len(voices.json()["items"]) == len(VOICES) > 0
    instructions = await api.get("/api/instructions")
    assert instructions.json() == {"text": FORMAT_RULES}


async def test_personas_list_with_leads_and_channels(api: ApiClient, world: World,
                                                     db: AsyncSession) -> None:
    archived = Persona(name="Архивная", is_active=False)
    other = Persona(name="Оля", ai_enabled=False)
    db.add_all([archived, other])
    await db.commit()
    response = await api.get("/api/personas")
    assert response.status_code == 200
    rows = response.json()["items"]
    assert [r["name"] for r in rows] == ["Марина", "Оля", "Архивная"]   # archived last
    marina = rows[0]
    assert (marina["leads_count"], marina["ai_enabled"], marina["is_active"]) == (1, True, True)
    [channel] = marina["channels"]
    assert (channel["status"], channel["account_label"]) == ("connected", "@marina")
    assert "credentials_enc" not in channel
    assert (rows[1]["leads_count"], rows[1]["ai_enabled"], rows[1]["channels"]) == (0, False, [])


async def test_archive_and_restore(api: ApiClient, world: World, db: AsyncSession) -> None:
    archived = await api.patch(pp(world), json={"is_active": False})
    assert archived.status_code == 200 and archived.json()["is_active"] is False
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None and not persona.is_active
    restored = await api.patch(pp(world), json={"is_active": True})
    assert restored.json()["is_active"] is True
    assert (await api.patch("/api/personas/999999", json={"is_active": True})).status_code == 404


async def test_rename_must_stay_unique(api: ApiClient, world: World, db: AsyncSession) -> None:
    db.add(Persona(name="Оля"))
    await db.commit()
    taken = await api.put(pp(world), json={"name": " Оля "})
    assert taken.status_code == 400 and "уже есть" in taken.json()["error"]
    assert (await api.put(pp(world), json={"name": "Марина  К"})).json()["name"] == "Марина К"


async def test_clone_copies_the_chosen_parts(api: ApiClient, world: World,
                                             db: AsyncSession) -> None:
    await api.put(pp(world), json={"prompt": "Ты Марина.", "guard_rules": "без фамилий",
                                   "language": "uk"})
    response = await api.post(pp(world, "/clone"), json={
        "name": "Оля", "parts": ["prompt", "models"]})
    assert response.status_code == 201, response.text
    clone = response.json()
    assert clone["name"] == "Оля" and clone["id"] != world.persona_id
    assert (clone["prompt"], clone["model_reply"]) == ("Ты Марина.", "google/reply")
    assert (clone["guard_rules"], clone["language"]) == ("", "ru")      # not ticked
    assert clone["is_active"] is True
    channels = await api.get(f"/api/personas/{clone['id']}/channels")
    assert channels.json()["items"] == []                               # never copied
    again = await api.post(pp(world, "/clone"), json={"name": "Оля", "parts": []})
    assert again.status_code == 400 and "уже есть" in again.json()["error"]
    bad = await api.post(pp(world, "/clone"), json={"name": "Катя", "parts": ["chats"]})
    assert bad.status_code == 400
    blank = await api.post(pp(world, "/clone"), json={"name": "  ", "parts": []})
    assert blank.status_code == 400
