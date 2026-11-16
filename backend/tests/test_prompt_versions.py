"""Versions of a persona prompt: every change is kept with who and when, an older text can be put
back in use, the history starts with the text that was there before it existed."""
from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy import select

from sim.adapters.db.models import Persona, PersonaPromptVersion
from sim.adapters.db.session import transaction
from sim.persona_prompts import CLI_AUTHOR, PERSONA_PROMPTS, apply_persona_prompts
from sim.services.prompt_versions import BEFORE_HISTORY, PromptVersions, VersionNotFound
from tests.conftest import OPERATOR, OWNER, login
from tests.test_migrations import alembic_config

INITIAL = "Ты тестовая персона.\n\nХарактер: тёплая. Биография: 20 лет в проектах."


async def save_prompt(api, persona_id: int, text: str, headers: dict):
    """The persona form saved with a new prompt (the prompt is a field of the persona)."""
    url = f"/api/personas/{persona_id}"
    body = {**(await api.get(url)).json(), "gender": "female", "agent_prompt": text}
    return await api.put(url, json=body, headers=headers)


async def versions_of(api, persona_id: int) -> list[dict]:
    return (await api.get(f"/api/personas/{persona_id}/prompt/versions")).json()["items"]


async def test_saving_the_prompt_keeps_every_text_newest_first(api, world):
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    assert await versions_of(api, world.persona.id) == []
    assert (await save_prompt(api, world.persona.id, "Версия 2", h)).status_code == 200
    assert (await save_prompt(api, world.persona.id, "Версия 2", h)).status_code == 200   # same: no version
    assert (await save_prompt(api, world.persona.id, "Версия 3", h)).status_code == 200
    items = await versions_of(api, world.persona.id)
    assert [(v["text"], v["author"], v["note"]) for v in items] == [
        ("Версия 3", "Test", ""),
        ("Версия 2", "Test", ""),
        (INITIAL, "", BEFORE_HISTORY),                 # the text in use before the history began
    ]
    assert items[0]["created_at"] and items[0]["id"] > items[1]["id"]


async def test_an_older_version_is_restored_as_the_newest(api, container, world):
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    url = f"/api/personas/{world.persona.id}/prompt"
    await save_prompt(api, world.persona.id, "Новая", h)
    first = (await versions_of(api, world.persona.id))[-1]
    resp = await api.post(f"{url}/versions/{first['id']}/restore", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_prompt"] == INITIAL
    assert [v["text"] for v in body["items"]] == [INITIAL, "Новая", INITIAL]
    assert body["items"][0]["note"].startswith("восстановлена версия от ")
    async with container.factory() as s:
        assert (await s.get(Persona, world.persona.id)).agent_prompt == INITIAL
    assert (await api.post(f"{url}/versions/999999/restore", headers=h)).status_code == 404


async def test_only_the_owner_restores(api, world):
    owner = {"X-CSRF-Token": await login(api, *OWNER)}
    await save_prompt(api, world.persona.id, "Новая", owner)
    version_id = (await versions_of(api, world.persona.id))[-1]["id"]
    operator = {"X-CSRF-Token": await login(api, *OPERATOR)}
    resp = await api.post(f"/api/personas/{world.persona.id}/prompt/versions/{version_id}/restore",
                          headers=operator)
    assert resp.status_code == 403


async def test_a_version_of_another_persona_is_not_found(container, world):
    async with transaction(container.factory) as s:
        other = Persona(name="Другая", agent_prompt="чужой текст")
        s.add(other)
        await s.flush()
        versions = PromptVersions(s)
        await versions.save(other, "чужой текст 2")
        foreign = (await versions.history(other.id))[0].id
        with pytest.raises(VersionNotFound):
            await versions.get(world.persona.id, foreign)


async def test_apply_persona_prompts_saves_a_version(container, world):
    name = next(iter(PERSONA_PROMPTS))
    async with transaction(container.factory) as s:
        (await s.get(Persona, world.persona.id)).name = name
    async with transaction(container.factory) as s:
        await apply_persona_prompts(s)
    async with container.factory() as s:
        rows = (await s.scalars(select(PersonaPromptVersion).order_by(PersonaPromptVersion.id))).all()
    assert [(r.text, r.author) for r in rows] == [(INITIAL, ""), (PERSONA_PROMPTS[name], CLI_AUTHOR)]


def test_0032_starts_the_history_with_the_prompt_in_use(tmp_path):
    db = tmp_path / "versions.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "0031")
    with sqlite3.connect(db) as con:
        for pid, name, prompt in ((1, "A", "текст A"), (2, "B", None)):
            con.execute(
                "INSERT INTO persona (id, name, gender, avatar_url, description, prohibitions, language, "
                "model, voice_id, voice_mode, voice_style, is_active, created_at, agent_prompt) "
                "VALUES (?, ?, '', '', '', '', 'ru', '', '', 'off', '', 1, '2026-11-01 00:00:00', ?)",
                (pid, name, prompt))
    command.upgrade(cfg, "0032")
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT persona_id, text, author, note FROM persona_prompt_version").fetchall()
    assert rows == [(1, "текст A", "", "версия на момент включения истории")]
    command.downgrade(cfg, "0031")
    command.upgrade(cfg, "head")
