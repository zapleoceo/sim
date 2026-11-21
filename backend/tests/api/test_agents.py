"""«Агенты» on the persona page (docs/agents.md): the registry, one persona's agents, save with
validation and the Broker catalog check, reset to the defaults."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona
from sim.domain.agent_prompts import GUARD_PROMPT
from sim.domain.agents import AGENTS
from sim.ports import ModelInfo
from sim.services.context.instructions import CALL_RULES, FORMAT_RULES
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp


def model(model_id: str, *, available: bool = True) -> ModelInfo:
    return ModelInfo(id=model_id, available=available, input_modalities=("text",),
                     output_modalities=("text",), capabilities=("chat",))


async def test_registry_lists_groups_agents_and_defaults(api: ApiClient) -> None:
    response = await api.get("/api/agents/registry")
    assert response.status_code == 200
    body = response.json()
    assert [(g["title"], g["agents"]) for g in body["groups"]] == [
        ("Ответ", ["attention", "reply", "guard", "vision", "transcribe", "tts"]),
        ("Медиа", ["image", "video"]),
        ("Звонки", ["live"]),
        ("Фон", ["life", "passport", "memory", "appearance"]),
    ]
    agents = {a["key"]: a for a in body["agents"]}
    assert len(agents) == len(AGENTS)
    assert agents["reply"]["key_tier"] == "paid" and agents["reply"]["prompt"] is None
    assert agents["reply"]["readonly_prompt"] == FORMAT_RULES
    assert agents["live"]["readonly_prompt"] == CALL_RULES
    assert agents["guard"]["prompt"] == GUARD_PROMPT
    assert agents["guard"]["placeholders"] == ["name", "language"]
    assert agents["guard"]["fallback"] == "reply" and agents["guard"]["max_tokens"] == 400
    assert agents["life"]["model"] == "codex-sub/gpt-6.1-sol"
    assert agents["life"]["key_tier"] == "auto"
    assert agents["vision"]["model_filter"] == [
        {"input": ["image"], "output": [], "capabilities": []},
        {"input": ["video"], "output": [], "capabilities": []}]
    assert agents["tts"]["port"] == "speech" and agents["tts"]["max_tokens"] is None
    assert [t["label"] for t in body["key_tiers"]] == ["Авто", "Платный", "Бесплатный"]
    assert body["max_tokens_range"] == {"min": 64, "max": 16384}


async def test_persona_agents_show_choices_and_effective_models(api: ApiClient,
                                                                world: World) -> None:
    body = (await api.get(pp(world, "/agents"))).json()
    reply, guard, life = (body["agents"][k] for k in ("reply", "guard", "life"))
    assert reply == {"model": "google/reply", "key_tier": "paid", "prompt": "",
                     "max_tokens": 4096, "effective_model": "google/reply"}
    # Nothing chosen: the guard borrows the companion's model.
    assert (guard["model"], guard["effective_model"]) == (None, "google/reply")
    # The planner is switched off for this persona ("" over its default model).
    assert (life["model"], life["effective_model"]) == ("", "")
    assert body["agents"]["tts"]["max_tokens"] is None
    assert body["guard_rules"] == ""
    assert body["attention"] == {"mode": "human", "pace": 1.0, "may_leave_read": True,
                                 "after_missed_call": True, "night_dnd": True}
    assert body["agents"]["attention"]["effective_model"] == "gemini/gemini-3.5-flash-lite"
    assert (await api.get("/api/personas/999999/agents")).status_code == 404


async def test_save_agents_stores_only_what_differs(api: ApiClient, world: World,
                                                    db: AsyncSession) -> None:
    response = await api.put(pp(world, "/agents"), json={
        "agents": {
            "guard": {"model": "google/guard", "key_tier": "free", "prompt": "Проверь {name}.",
                      "max_tokens": 300},
            "life": {"model": None, "key_tier": "auto", "prompt": "", "max_tokens": 6000},
            "memory": {"model": "", "key_tier": "auto", "prompt": "  ", "max_tokens": None},
        },
        "guard_rules": "Не называть фамилию."})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agents"]["guard"] == {
        "model": "google/guard", "key_tier": "free", "prompt": "Проверь {name}.",
        "max_tokens": 300, "effective_model": "google/guard"}
    # «By default» again: the planner runs on its registry model.
    assert body["agents"]["life"]["effective_model"] == "codex-sub/gpt-6.1-sol"
    assert body["guard_rules"] == "Не называть фамилию."
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None
    await db.refresh(persona)
    # The chronicler has a default model (docs/memory.md), so "" is «не использовать».
    assert body["agents"]["memory"]["effective_model"] == ""
    assert persona.agents == {
        "reply": {"model": "google/reply"},
        "guard": {"model": "google/guard", "key_tier": "free", "prompt": "Проверь {name}.",
                  "max_tokens": 300},
        "memory": {"model": ""},
    }


async def test_save_agents_rejects_what_an_agent_does_not_have(api: ApiClient,
                                                               world: World) -> None:
    for agents in ({"boss": {}},
                   {"tts": {"max_tokens": 500}},
                   {"transcribe": {"prompt": "слушай внимательно"}},
                   {"guard": {"key_tier": "cheap"}},
                   {"guard": {"max_tokens": 10}},
                   {"guard": {"max_tokens": 20000}},
                   {"guard": {"temperature": 1}}):
        response = await api.put(pp(world, "/agents"), json={"agents": agents})
        assert response.status_code == 400, agents
        assert "error" in response.json()


async def test_changed_models_are_checked_against_a_readable_catalog(
        api: ApiClient, world: World, deps: FakeDeps) -> None:
    deps.llm.models = [model("google/other"), model("google/guard", available=False)]
    unknown = await api.put(pp(world, "/agents"),
                            json={"agents": {"guard": {"model": "nobody/model"}}})
    assert unknown.status_code == 400 and "nobody/model" in unknown.json()["error"]
    # Listed but unavailable right now: saves. An unchanged model that left the catalog does
    # not block other edits.
    saved = await api.put(pp(world, "/agents"), json={"agents": {
        "guard": {"model": "google/guard"}, "reply": {"model": "google/reply"}}})
    assert saved.status_code == 200, saved.text
    deps.llm.fail = True
    down = await api.put(pp(world, "/agents"), json={"agents": {"tts": {"model": "a/tts"}}})
    assert down.status_code == 200
    assert down.json()["agents"]["tts"]["effective_model"] == "a/tts"


ATTENTION = {"mode": "free_only", "pace": 1.5, "may_leave_read": False,
             "after_missed_call": True, "night_dnd": False}


async def test_attention_settings_save_with_the_agents_and_reset_with_its_card(
        api: ApiClient, world: World, db: AsyncSession) -> None:
    saved = await api.put(pp(world, "/agents"), json={
        "agents": {"attention": {"model": None, "key_tier": "paid", "prompt": "",
                                 "max_tokens": 1500}},
        "attention": ATTENTION})
    assert saved.status_code == 200, saved.text
    assert saved.json()["attention"] == ATTENTION
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None
    await db.refresh(persona)
    assert persona.attention == ATTENTION
    # Without `attention` in the body the settings stay.
    again = await api.put(pp(world, "/agents"), json={"agents": {}})
    assert again.json()["attention"] == ATTENTION

    for bad in ({**ATTENTION, "mode": "robot"}, {**ATTENTION, "pace": 3},
                {**ATTENTION, "extra": 1}, {"mode": "human"}):
        response = await api.put(pp(world, "/agents"), json={"agents": {}, "attention": bad})
        assert response.status_code == 400, bad

    reset = await api.post(pp(world, "/agents/attention/reset"))
    assert reset.json()["attention"]["mode"] == "human"
    await db.refresh(persona)
    assert persona.attention == {}


async def test_reset_returns_an_agent_to_its_defaults(api: ApiClient, world: World) -> None:
    await api.put(pp(world, "/agents"), json={"agents": {
        "guard": {"model": "google/guard", "key_tier": "free", "prompt": "Свой промт"}}})
    response = await api.post(pp(world, "/agents/guard/reset"))
    assert response.status_code == 200
    guard = response.json()["agents"]["guard"]
    assert guard == {"model": None, "key_tier": "paid", "prompt": "", "max_tokens": 400,
                     "effective_model": "google/reply"}
    assert (await api.post(pp(world, "/agents/boss/reset"))).status_code == 404
