"""Owner settings in the DB: defaults, validation, use by the persona prompt and the guard, API."""
from __future__ import annotations

import pytest

from sim.adapters.db.models import AppSetting
from sim.adapters.db.session import transaction
from sim.services.app_settings import BY_KEY, SPECS, AppSettingsService, SettingError, env_defaults
from sim.services.guard import GUARD_RULES
from sim.services.prompt import HISTORY_LIMIT, STYLE_RULE, VOICE_RULE
from tests.conftest import OPERATOR, OWNER, login


async def test_defaults_come_from_code_and_env(container):
    async with transaction(container.factory) as s:
        cfg = await AppSettingsService(s, env_defaults(container.settings)).load()
    assert cfg.voice_rule == VOICE_RULE and cfg.style_rule == STYLE_RULE
    assert cfg.guard_rules == GUARD_RULES and cfg.history_limit == HISTORY_LIMIT
    assert cfg.guard_capability == container.settings.guard_capability
    assert cfg.reply_debounce_s == container.settings.reply_debounce_s


async def test_set_validates_and_reset_returns_the_default(container):
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        item = await svc.set("style_rule", "  пиши коротко  ", admin_id=None)
        assert item.value == "пиши коротко" and not item.is_default
        for key, bad in (("history_limit", 2), ("history_limit", "40"), ("history_limit", True),
                         ("style_rule", "   "), ("guard_capability", "Chat Sales")):
            with pytest.raises(SettingError):
                await svc.set(key, bad, admin_id=None)
        with pytest.raises(SettingError):
            await svc.set("nope", "x", admin_id=None)
        item = await svc.reset("style_rule")
        assert item.value == STYLE_RULE and item.is_default


async def test_a_stored_value_that_no_longer_validates_falls_back_to_the_default(container):
    async with transaction(container.factory) as s:
        s.add(AppSetting(key="history_limit", value=99999))
        await s.flush()
        assert (await AppSettingsService(s).load()).history_limit == HISTORY_LIMIT


def test_every_spec_default_is_valid():
    for spec in SPECS:
        if spec.default is not None:
            assert spec.validate(spec.default) == spec.default, spec.key


async def test_prompt_and_guard_use_the_owner_texts(container, world, fake_llm):
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        await svc.set("voice_rule", "ROLE FROM DB", None)
        await svc.set("guard_rules", "GUARD RULES FROM DB", None)
        await svc.set("guard_capability", "chat:smart", None)
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "Привет!")
    system = fake_llm.gen_calls[0][0][0]["content"]
    assert "ROLE FROM DB" in system and VOICE_RULE not in system
    assert "never claim to be human" in system          # the honesty floor is not editable
    guard_messages, guard_kwargs = fake_llm.guard_calls[0]
    assert "GUARD RULES FROM DB" in guard_messages[0]["content"]
    assert "must not claim to be human" in guard_messages[0]["content"]
    assert guard_kwargs["capability"] == "chat:smart"


async def test_history_limit_trims_what_the_model_sees(container, world, fake_llm):
    from sim.services.prompt import Turn

    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("history_limit", 6, None)
    history = [Turn("user" if i % 2 == 0 else "assistant", f"t{i}") for i in range(20)]
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "последнее", history)
    turns = fake_llm.gen_calls[0][0][1:-1]
    assert len(turns) == 6 and turns[-1]["content"] == "последнее"


async def test_api_lists_and_owner_edits_settings(api):
    csrf = await login(api, OWNER[0])
    listed = (await api.get("/api/settings")).json()["items"]
    assert [i["key"] for i in listed] == [spec.key for spec in SPECS]
    assert all(i["is_default"] for i in listed)
    headers = {"X-CSRF-Token": csrf}
    resp = await api.put("/api/settings/style_rule", json={"value": "коротко"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["value"] == "коротко"
    assert resp.json()["updated_by_id"] is not None
    assert (await api.put("/api/settings/history_limit", json={"value": 1}, headers=headers)).status_code == 422
    assert (await api.put("/api/settings/nope", json={"value": 1}, headers=headers)).status_code == 404
    resp = await api.delete("/api/settings/style_rule", headers=headers)
    assert resp.json()["value"] == BY_KEY["style_rule"].default and resp.json()["is_default"]


async def test_api_operator_can_read_but_not_edit(api):
    csrf = await login(api, OPERATOR[0])
    assert (await api.get("/api/settings")).status_code == 200
    resp = await api.put("/api/settings/style_rule", json={"value": "x"}, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 403
