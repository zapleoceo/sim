"""Owner settings in the DB: defaults, validation, use by the persona prompt and the guard, API."""
from __future__ import annotations

import pytest

from sim.adapters.db.models import AppSetting
from sim.adapters.db.session import transaction
from sim.services.app_settings import BY_KEY, SPECS, AppSettingsService, SettingError, env_defaults
from sim.services.guard import GUARD_PROMPT, identity_control
from sim.services.instructions import (
    FACT_CONTROL,
    GUARD_CONTROL,
    IDENTITY_BOUNDARY,
    MEDIA_CONTROL,
    VOICE_CONTROL,
)
from sim.services.prompt import (
    HISTORY_LIMIT,
    OUTPUT_CONTRACT,
    RETRY_NUDGE,
    PersonaProfile,
    StrategyProfile,
    stable_system_prompt,
)
from tests.conftest import OPERATOR, OWNER, login


async def test_defaults_come_from_code_and_env(container):
    async with transaction(container.factory) as s:
        cfg = await AppSettingsService(s, env_defaults(container.settings)).load()
    assert cfg.output_contract == OUTPUT_CONTRACT and cfg.retry_nudge == RETRY_NUDGE
    assert cfg.guard_prompt == GUARD_PROMPT and cfg.history_limit == HISTORY_LIMIT
    assert cfg.guard_capability == container.settings.guard_capability
    assert cfg.reply_debounce_s == container.settings.reply_debounce_s
    assert cfg.incoming_media_subscription == 0


async def test_set_validates_and_reset_returns_the_default(container):
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        item = await svc.set("retry_nudge", "  пиши коротко  ", admin_id=None)
        assert item.value == "пиши коротко" and not item.is_default
        for key, bad in (("history_limit", 2), ("history_limit", "40"), ("history_limit", True),
                         ("retry_nudge", "   "), ("guard_capability", "Chat Sales")):
            with pytest.raises(SettingError):
                await svc.set(key, bad, admin_id=None)
        with pytest.raises(SettingError):
            await svc.set("nope", "x", admin_id=None)
        item = await svc.reset("retry_nudge")
        assert item.value == RETRY_NUDGE and item.is_default


def test_shared_rules_come_in_the_order_they_act_and_owner_accounts_are_one_group():
    helpers = {"Охранник", "Паспортистка", "Саммари диалога"}
    groups = list(dict.fromkeys(spec.group for spec in SPECS
                                if spec.group not in helpers and not spec.group.startswith("Промпт ·")))
    assert groups == ["Входящие вложения", "Ответы", "Время ответа по распорядку", "Стиль сообщений",
                      "Фото и кружочки персонажа", "Аккаунты владельца", "Журнал"]
    assert SPECS[0].group.startswith("Промпт · 1.")   # the model's texts come first, in the order they act
    owner = [spec.key for spec in SPECS if spec.group == "Аккаунты владельца"]
    assert owner == ["contact_profile_channel_id", "instant_reply_telegram_ids", "video_note_owner_telegram_id"]
    assert BY_KEY["contact_profile_channel_id"].default == 0
    assert BY_KEY["media_priority_telegram_ids"].default == ""


async def test_owner_accounts_are_a_list_of_numeric_telegram_ids(container):
    spec = BY_KEY["instant_reply_telegram_ids"]
    assert (spec.kind, spec.default, spec.group) == ("ids", "", "Аккаунты владельца")
    assert spec.validate(" 169510539,42 42;\n7 ") == "169510539, 42, 7"
    assert spec.validate("  ") == ""
    for bad in ("@dima", "0", "12a", "-5", 42, None):
        with pytest.raises(SettingError):
            spec.validate(bad)
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        assert (await svc.load()).instant_reply_telegram_ids == ""
        await svc.set("instant_reply_telegram_ids", "169510539", None)
        assert (await svc.load()).instant_reply_telegram_ids == "169510539"


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
    from sim.adapters.db.models import Persona

    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        await svc.set("output_contract", 'FORMAT FROM DB: {"reply": "", "stage": ""}', None)
        await svc.set("guard_prompt", "GUARD PROMPT FROM DB", None)
        await svc.set("guard_capability", "chat:smart", None)
        (await s.get(Persona, world.persona.id)).agent_prompt = "PERSONA PROMPT FROM DB"
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(await s.get(Persona, world.persona.id), "Привет!")
    system = fake_llm.gen_calls[0][0][0]["content"]
    assert system.startswith("PERSONA PROMPT FROM DB\n\n")
    assert 'FORMAT FROM DB: {"reply": "", "stage": ""}' in system and OUTPUT_CONTRACT not in system
    assert system.endswith(IDENTITY_BOUNDARY)
    guard_messages, guard_kwargs = fake_llm.guard_calls[0]
    assert guard_messages[0]["content"] == f"GUARD PROMPT FROM DB\n\n{GUARD_CONTROL}\n\n{FACT_CONTROL}\n\n{VOICE_CONTROL}\n\n{MEDIA_CONTROL}\n\n{identity_control()}"
    assert guard_kwargs["capability"] == "chat:smart"


async def test_the_retry_nudge_comes_from_the_settings(container, world, fake_llm):
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("retry_nudge", "NUDGE FROM DB", None)
    fake_llm.replies = ["not json"]
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "Привет!")
    retry_messages = fake_llm.gen_calls[1][0]
    assert retry_messages[-1] == {"role": "system", "content": "NUDGE FROM DB"}


async def test_api_has_no_fixed_texts_and_the_honesty_boundary_is_editable(api, world):
    await login(api, OWNER[0])
    body = (await api.get("/api/settings")).json()
    assert "fixed" not in body
    keys = {item["key"] for item in body["items"]}
    assert {"identity_boundary", "guard_control", "guard_identity_rule"} <= keys
    system = stable_system_prompt(PersonaProfile(name="Марина", agent_prompt="Ты Марина."), StrategyProfile())
    assert OUTPUT_CONTRACT in system and system.endswith(IDENTITY_BOUNDARY)


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
    resp = await api.put("/api/settings/retry_nudge", json={"value": "коротко"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["value"] == "коротко"
    assert resp.json()["updated_by_id"] is not None
    assert (await api.put("/api/settings/history_limit", json={"value": 1}, headers=headers)).status_code == 422
    assert (await api.put("/api/settings/nope", json={"value": 1}, headers=headers)).status_code == 404
    resp = await api.delete("/api/settings/retry_nudge", headers=headers)
    assert resp.json()["value"] == BY_KEY["retry_nudge"].default and resp.json()["is_default"]


async def test_api_operator_can_read_but_not_edit(api):
    csrf = await login(api, OPERATOR[0])
    assert (await api.get("/api/settings")).status_code == 200
    resp = await api.put("/api/settings/retry_nudge", json={"value": "x"}, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 403


async def test_guard_model_pins_the_first_guard_call_but_not_the_fallback(container, world, fake_llm):
    from sim.ports.llm import LLMResult

    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        with pytest.raises(SettingError):
            await svc.set("guard_model", "not a model id!", None)
        await svc.set("guard_model", "codex-sub/gpt-6.1-terra", None)
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "Привет!")
    assert fake_llm.guard_calls[0][1]["model"] == "codex-sub/gpt-6.1-terra"

    # the same model as the generator: the fallback route never pins a model
    fake_llm.calls.clear()
    fake_llm.guard_replies = [LLMResult(text='{"verdict": "ok", "violations": [], "fixed_reply": ""}',
                                        model="fake-model"), ]
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "Ещё раз")
    first, fallback = fake_llm.guard_calls[:2]
    assert first[1]["model"] == "codex-sub/gpt-6.1-terra" and fallback[1]["model"] is None


def test_empty_guard_model_means_the_route_decides():
    from sim.services.app_settings import BY_KEY

    assert BY_KEY["guard_model"].validate("  ") == ""
