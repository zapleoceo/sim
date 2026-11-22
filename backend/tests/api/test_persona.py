"""Persona page: persona read/update, the models and voices catalogs, archive, clone. The agents
(models, key tiers, prompts) are in test_agents.py."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import LlmCall, Persona
from sim.domain.enums import LlmPurpose
from sim.ports import ModelInfo, PortError
from sim.services.voices import GEMINI_VOICES, SAMPLE_TEXT, SAMPLES, SampleCache
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp
from tests.fakes.ai import FakeSpeech


def model(model_id: str, *, available: bool = True) -> ModelInfo:
    return ModelInfo(id=model_id, available=available, input_modalities=("text", "image"),
                     output_modalities=("text",), capabilities=("chat", "json"))


async def test_get_persona(api: ApiClient, world: World) -> None:
    response = await api.get(pp(world))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == world.persona_id
    assert body["name"] == "Марина"
    assert body["reply_delay_s"] == 8
    assert "created_at" not in body and "updated_at" not in body
    # Agents and the guard's rules have their own path (/agents).
    assert not any(key.startswith("model_") for key in body)
    assert "agents" not in body and "guard_rules" not in body


async def test_persona_404_when_unknown(api: ApiClient) -> None:
    response = await api.get("/api/personas/999999")
    assert response.status_code == 404
    assert "error" in response.json()
    assert (await api.get("/api/personas")).json() == {"items": []}


async def test_update_persona_partial(api: ApiClient, world: World, db: AsyncSession) -> None:
    response = await api.put(pp(world), json={
        "prompt": "Новый промт", "birth_date": "2001-05-17", "timezone": "Europe/Kyiv",
        "photo_enabled": False, "id": 12345, "created_at": "ignored",
        "model_reply": "ignored/now", "guard_rules": "ignored here"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["prompt"], body["birth_date"], body["photo_enabled"]) == (
        "Новый промт", "2001-05-17", False)
    assert body["name"] == "Марина"
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None and persona.prompt == "Новый промт"
    assert persona.guard_rules == ""
    assert persona.agents["reply"] == {"model": "google/reply"}


async def test_update_persona_rejects_bad_values(api: ApiClient, world: World) -> None:
    for payload in ({"timezone": "Mars/Base"}, {"reply_delay_s": -1}, {"name": ""},
                    {"avatar_media_id": 999999}):
        response = await api.put(pp(world), json=payload)
        assert response.status_code == 400, payload
        assert "error" in response.json()


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


async def test_voices(api: ApiClient) -> None:
    voices = await api.get("/api/voices")
    assert voices.status_code == 200
    items = voices.json()["items"]
    assert len(items) == len(GEMINI_VOICES) > 0
    assert all(set(item) == {"id", "label", "gender", "tone"} for item in items)
    assert {item["gender"] for item in items} == {"female", "male"}
    by_id = {item["id"]: item for item in items}
    assert by_id["Vindemiatrix"] == {"id": "Vindemiatrix", "gender": "female", "tone": "нежный",
                                     "label": "Vindemiatrix — нежный, женский"}
    assert by_id["Puck"]["label"] == "Puck — бодрый, мужской"


@pytest.fixture
def samples() -> Iterator[SampleCache]:
    """The process-wide cache of voice samples, empty for each test."""
    SAMPLES.items.clear()
    yield SAMPLES
    SAMPLES.items.clear()


async def with_tts(db: AsyncSession, world: World) -> None:
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None
    persona.agents = {**persona.agents, "tts": {"model": "google/tts"}}
    await db.commit()


async def test_a_voice_sample_is_spoken_once(api: ApiClient, world: World, db: AsyncSession,
                                             deps: FakeDeps, samples: SampleCache) -> None:
    deps.speech = FakeSpeech()
    await with_tts(db, world)
    url = f"/api/voices/Kore/sample?persona_id={world.persona_id}"

    first = await api.get(url)

    assert first.status_code == 200, first.text
    assert first.content == b"RIFF" + SAMPLE_TEXT.encode()
    assert first.headers["content-type"].startswith("audio/wav")
    assert deps.speech.calls == [{"text": SAMPLE_TEXT, "voice": "Kore", "style": "",
                                  "model": "google/tts", "key_tier": "paid"}]
    [log] = (await db.scalars(select(LlmCall).where(LlmCall.purpose == LlmPurpose.TTS))).all()
    assert log.request["voice"] == "Kore" and log.request["sample"] is True
    # Cached in the process: the next listen costs nothing.
    again = await api.get(url)
    assert again.status_code == 200 and again.content == first.content
    assert len(deps.speech.calls) == 1
    other = await api.get(f"/api/voices/Puck/sample?persona_id={world.persona_id}")
    assert other.status_code == 200 and len(deps.speech.calls) == 2


async def test_a_voice_sample_needs_a_voice_and_a_model(api: ApiClient, world: World,
                                                        db: AsyncSession, deps: FakeDeps,
                                                        samples: SampleCache) -> None:
    deps.speech = FakeSpeech()
    assert (await api.get("/api/voices/Alice/sample")).status_code == 404
    no_model = await api.get(f"/api/voices/Kore/sample?persona_id={world.persona_id}")
    assert no_model.status_code == 400 and "Голос" in no_model.json()["error"]
    assert (await api.get("/api/voices/Kore/sample")).status_code == 400   # no persona: default
    assert (await api.get("/api/voices/Kore/sample?persona_id=999999")).status_code == 404
    await with_tts(db, world)
    deps.speech.error = PortError("no paid key")
    failed = await api.get(f"/api/voices/Kore/sample?persona_id={world.persona_id}")
    assert failed.status_code == 502 and "no paid key" in failed.json()["error"]
    assert samples.items == {}                          # a failure is not cached


def test_the_sample_cache_drops_the_oldest() -> None:
    cache = SampleCache(size=2)
    cache.put("m", "Kore", b"1", "audio/wav")
    cache.put("m", "Puck", b"2", "audio/wav")
    assert cache.get("m", "Kore") == (b"1", "audio/wav")   # now the newest
    cache.put("m", "Leda", b"3", "audio/wav")
    assert cache.get("m", "Puck") is None
    assert cache.get("m", "Kore") is not None and cache.get("other", "Kore") is None


async def test_her_voice_speed(api: ApiClient, world: World, db: AsyncSession) -> None:
    assert (await api.get(pp(world))).json()["voice_speed"] == 1.0
    saved = await api.put(pp(world), json={"voice_speed": 1.25})
    assert saved.status_code == 200, saved.text
    assert saved.json()["voice_speed"] == 1.25
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None and persona.voice_speed == 1.25
    for speed in (2.0, 0.5):
        assert (await api.put(pp(world), json={"voice_speed": speed})).status_code == 400


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
    await api.put(pp(world), json={"prompt": "Ты Марина.", "language": "uk"})
    await api.put(pp(world, "/agents"), json={
        "agents": {"guard": {"key_tier": "free"}}, "guard_rules": "без фамилий"})
    response = await api.post(pp(world, "/clone"), json={
        "name": "Оля", "parts": ["prompt", "agents"]})
    assert response.status_code == 201, response.text
    clone = response.json()
    assert clone["name"] == "Оля" and clone["id"] != world.persona_id
    assert (clone["prompt"], clone["language"]) == ("Ты Марина.", "ru")   # basics not ticked
    assert clone["is_active"] is True
    agents = (await api.get(f"/api/personas/{clone['id']}/agents")).json()
    assert agents["agents"]["reply"]["model"] == "google/reply"
    assert agents["agents"]["guard"]["key_tier"] == "free"
    assert agents["guard_rules"] == ""                                  # «guard» not ticked
    channels = await api.get(f"/api/personas/{clone['id']}/channels")
    assert channels.json()["items"] == []                               # never copied
    again = await api.post(pp(world, "/clone"), json={"name": "Оля", "parts": []})
    assert again.status_code == 400 and "уже есть" in again.json()["error"]
    bad = await api.post(pp(world, "/clone"), json={"name": "Катя", "parts": ["models"]})
    assert bad.status_code == 400
    blank = await api.post(pp(world, "/clone"), json={"name": "  ", "parts": []})
    assert blank.status_code == 400
