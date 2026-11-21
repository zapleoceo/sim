"""Persona page: persona read/update with model checks, models, voices, instructions, channels."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona
from sim.ports import ModelInfo
from sim.services.context.instructions import FORMAT_RULES
from sim.services.voices import GEMINI_VOICES as VOICES
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World


def model(model_id: str, *, available: bool = True) -> ModelInfo:
    return ModelInfo(id=model_id, available=available, input_modalities=("text", "image"),
                     output_modalities=("text",), capabilities=("chat", "json"))


async def test_get_persona(api: ApiClient, world: World) -> None:
    response = await api.get("/api/persona")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == world.persona_id
    assert body["name"] == "Марина"
    assert body["model_reply"] == "google/reply"
    assert body["reply_delay_s"] == 8
    assert "created_at" not in body and "updated_at" not in body


async def test_persona_404_when_not_created(api: ApiClient) -> None:
    response = await api.get("/api/persona")
    assert response.status_code == 404
    assert "error" in response.json()


async def test_update_persona_partial(api: ApiClient, world: World, db: AsyncSession) -> None:
    response = await api.put("/api/persona", json={
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
        response = await api.put("/api/persona", json=payload)
        assert response.status_code == 400, payload
        assert "error" in response.json()


async def test_model_ids_are_checked_against_a_readable_catalog(api: ApiClient, world: World,
                                                                deps: FakeDeps) -> None:
    deps.llm.models = [model("google/reply"), model("google/guard", available=False)]
    unknown = await api.put("/api/persona", json={"model_guard": "nobody/model"})
    assert unknown.status_code == 400
    assert "nobody/model" in unknown.json()["error"]
    # A listed model saves even when the catalog says it is unavailable right now.
    unavailable = await api.put("/api/persona", json={"model_guard": "google/guard"})
    assert unavailable.status_code == 200
    assert unavailable.json()["model_guard"] == "google/guard"
    # Clearing a model is always allowed.
    assert (await api.put("/api/persona", json={"model_guard": ""})).status_code == 200


async def test_unchanged_model_missing_from_catalog_does_not_block(api: ApiClient, world: World,
                                                                   deps: FakeDeps) -> None:
    deps.llm.models = [model("google/other")]
    response = await api.put("/api/persona", json={"model_reply": "google/reply",
                                                   "prompt": "ещё"})
    assert response.status_code == 200


async def test_models_saved_unchecked_when_catalog_is_empty_or_down(
        api: ApiClient, world: World, deps: FakeDeps) -> None:
    assert (await api.put("/api/persona", json={"model_tts": "a/tts"})).status_code == 200
    deps.llm.fail = True
    response = await api.put("/api/persona", json={"model_image": "b/image"})
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


async def test_channels_without_credentials(api: ApiClient, world: World) -> None:
    response = await api.get("/api/channels")
    assert response.status_code == 200
    [channel] = response.json()["items"]
    assert channel["id"] == world.channel_id
    assert (channel["kind"], channel["status"], channel["account_label"]) == (
        "telegram_user", "connected", "@marina")
    assert "credentials_enc" not in channel
