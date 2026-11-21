"""Every text the code adds to a model request is an owner setting (sim.services.instructions):
the defaults are the former code texts, the owner's stored text reaches the request, and the
settings API has nothing fixed or hidden."""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import MediaKind, Stage
from sim.services import instructions
from sim.services.app_settings import BY_KEY, SPECS, AppSettingsService
from sim.services.call_prompt import call_identity, call_system_prompt, greeting
from sim.services.content import caption_prompt
from sim.services.context_window import Window
from sim.services.followup import followup_instruction
from sim.services.guard import guard_system_prompt
from sim.services.persona_media_tool import Offer, ShotEnvironment, shot_prompt
from sim.services.prompt import (
    DialogContext,
    MediaAsk,
    PersonaProfile,
    StrategyProfile,
    Turn,
    build_messages,
    stable_system_prompt,
)
from sim.services.reply_schedule import WORK, delay_note
from tests.conftest import OWNER, decision_json, login

NOW = datetime(2026, 11, 20, 12, 0)
CTX = DialogContext(
    stage=Stage.FIRST_CONTACT, status="active", client_name="Ира", channel_kind="telegram",
    ref_code="r1", now=NOW, persona_facts="- Сейчас у тебя: пятница", passport="Любит кофе",
    summary="Говорили о курсе", summary_until=NOW, journal=["- 2026-11-20 10:00 заметки: кофе"],
    handoff_links=[("whatsapp", "https://wa.me/1")], contact_photo="data:image/png;base64,AA",
)
PERSONA = PersonaProfile(name="Марина", agent_prompt="Ты Марина.", routine="ежедневно 23:00-06:30 сплю")
STRATEGY = StrategyProfile()
MARINA = Persona(name="Марина", gender="female", birth_date=None, timezone="UTC",
                 agent_prompt="Ты Марина.", language="ru")
WORK_DELAY = {"cls": WORK, "what": "консультации", "until": "2026-11-20T13:00:00", "urgent": None}


def chat_request() -> str:
    messages = build_messages(PERSONA, STRATEGY, [Turn("user", "Привет")], CTX)
    return "\n".join(str(message["content"]) for message in messages)


def call_request() -> str:
    long_chat = [Turn("user", "слово " * 400), Turn("assistant", "ответ")]
    return call_system_prompt(PERSONA, STRATEGY, long_chat, CTX) + "\n" + greeting("Марина")


def delay_request() -> str:
    later = datetime(2026, 11, 20, 15, 0)
    return "\n".join([delay_note({"pause": True}, NOW, later, "UTC"),
                      delay_note(WORK_DELAY, datetime(2026, 11, 20, 9, 0), later, "UTC")])


def shot_request() -> str:
    environment = ShotEnvironment.capture("UTC", NOW)
    return "\n".join(shot_prompt(MARINA, MediaAsk(kind, "на кухне"), environment)
                     for kind in (MediaKind.PHOTO, MediaKind.VIDEO_NOTE))


def caption_request() -> str:
    return "\n".join(m["content"] for idea in ("", "осень") for m in caption_prompt(MARINA, "photo", idea))


# Where each text lands, checked through the pure builders of the request.
BUILDERS: dict[str, Callable[[], str]] = {
    **dict.fromkeys(("link_contract", "unified_boundaries", "identity_boundary", "routine_header",
                     "facts_header", "context_header", "known_contact_rule", "handoff_links_header",
                     "contact_photo_note", "passport_header", "summary_header", "journal_header"),
                    chat_request),
    **dict.fromkeys(("call_rules", "call_identity", "call_greeting", "call_history_header",
                     "call_history_trimmed"), call_request),
    **dict.fromkeys(("delay_pause_note", "delay_routine_hint", "delay_pause_hint"), delay_request),
    **dict.fromkeys(("guard_control", "guard_fact_control", "guard_voice_control",
                     "guard_media_control", "guard_identity_rule"), lambda: guard_system_prompt("Охранник.")),
    "history_trimmed": lambda: Window([], NOW, hours=48, trimmed=3).note(),
    "followup_event": lambda: followup_instruction([1.0, 24.0], 0),
    "media_none": lambda: Offer().brief(),
    "media_brief": lambda: Offer((MediaKind.PHOTO,)).brief(),
    **dict.fromkeys(("photo_frame", "video_note_frame", "shot_person", "shot_light", "shot_rules"),
                    shot_request),
    **dict.fromkeys(("caption_intro", "caption_request", "caption_default_idea", "caption_rules"),
                    caption_request),
}


@pytest.mark.parametrize("key", sorted(BUILDERS))
async def test_the_owner_text_reaches_the_model_request(container, key):
    marker = f"ТЕКСТ ВЛАДЕЛЬЦА {key}"
    assert marker not in BUILDERS[key]()
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set(key, marker, None)
    if key == "caption_rules":   # threaded through the loaded settings, not the published texts
        async with transaction(container.factory) as s:
            rules = (await AppSettingsService(s).load()).caption_rules
        assert marker in caption_prompt(MARINA, "photo", "", rules)[0]["content"]
        return
    assert marker in BUILDERS[key]()


async def test_a_reset_returns_the_former_text(container):
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        await svc.set("call_rules", "ЗВОНОК ПО-НОВОМУ", None)
        assert "ЗВОНОК ПО-НОВОМУ" in call_request()
        await svc.reset("call_rules")
    assert instructions.CALL_RULES in call_request()


def test_every_instruction_is_an_owner_setting_with_the_code_default():
    prompt_keys = [spec.key for spec in SPECS if spec.group.startswith("Промпт ·")]
    assert set(prompt_keys) == set(instructions.DEFAULTS) | {"output_contract", "retry_nudge", "caption_rules"}
    for key, default in instructions.DEFAULTS.items():
        spec = BY_KEY[key]
        assert (spec.kind, spec.default) == ("text", default), key
        assert spec.label and spec.help, key
    groups = list(dict.fromkeys(spec.group for spec in SPECS if spec.group.startswith("Промпт ·")))
    assert groups == ["Промпт · 1. Ответ в чате: правила", "Промпт · 2. Ответ в чате: контекст",
                      "Промпт · 3. Звонок", "Промпт · 4. Проверка охранником",
                      "Промпт · 5. Напоминания и продолжения", "Промпт · 6. Фото и кружочки",
                      "Промпт · 7. Вложения и подписи постов"]


def test_every_instruction_is_read_by_the_code_that_builds_a_request():
    """No orphan setting: each key is read through `instructions.text/fill` somewhere in sim/."""
    sources = "\n".join(path.read_text(encoding="utf-8") for path in Path("sim").rglob("*.py")
                        if path.name not in ("instructions.py", "app_settings.py"))
    used = set(re.findall(r"""instructions\.(?:text|fill)\(\s*["']([a-z_]+)["']""", sources))
    used |= {"photo_frame", "video_note_frame"}   # picked by kind: FRAMES in persona_media_tool
    used |= {f"guard_{name}" for name in ("control", "fact_control", "voice_control", "media_control")}
    assert set(instructions.DEFAULTS) <= used, set(instructions.DEFAULTS) - used


def test_the_defaults_keep_the_former_wording():
    assert call_identity("Марина").startswith("КТО ТЫ НА ЗВОНКЕ: ты тот же персонаж Марина, что и")
    assert greeting("Марина") == instructions.CALL_GREETING
    brief = Offer((MediaKind.PHOTO, MediaKind.VIDEO_NOTE)).brief()
    assert 'JSON поле "media": {"kind": "photo" или "video_note", "scene": "что в кадре' in brief
    assert brief.startswith("МЕДИА: к этому ответу ты можешь приложить своё фото или свой кружочек")
    assert guard_system_prompt("П").endswith(
        instructions.IDENTITY_BOUNDARY + " " + instructions.GUARD_IDENTITY_RULE)
    assert followup_instruction([1.0, 24.0], 1) == (
        "СОБЫТИЕ: плановое напоминание 2 из 2; клиент не отвечает примерно 24 ч. Последнее: да. "
        "Сформируй ответ по единому промпту агента.")
    assert Window([], NOW, hours=48, trimmed=3).note().startswith(
        "ИСТОРИЯ ОБРЕЗАНА: за последние 48 ч сообщений больше")
    system = stable_system_prompt(PERSONA, STRATEGY)
    assert system.endswith(instructions.IDENTITY_BOUNDARY)


def test_placeholders_are_filled_once_and_json_braces_stay():
    instructions.publish({"call_identity": "Я {name}, {unknown} и {\"kind\": 1}"})
    assert call_identity("{name}") == 'Я {name}, {unknown} и {"kind": 1}'


async def test_the_repair_after_a_rejected_answer_uses_the_owner_text(container, world, fake_llm):
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("repair_nudge", "ПОВТОРИ ({code})", None)
    fake_llm.replies = ["not json", "still not json", decision_json()]
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "Привет!")
    assert fake_llm.gen_calls[2][0][-1] == {"role": "system", "content": "ПОВТОРИ (format)"}


async def test_the_guard_request_uses_every_owner_rule(container, world, fake_llm):
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        await svc.set("guard_media_control", "МЕДИА ПРАВИЛО ВЛАДЕЛЬЦА", None)
        await svc.set("identity_boundary", "ГРАНИЦА ВЛАДЕЛЬЦА", None)
    async with transaction(container.factory) as s:
        await container.conversation(s).preview(world.persona, "Привет!")
    guard_system = fake_llm.guard_calls[0][0][0]["content"]
    assert "МЕДИА ПРАВИЛО ВЛАДЕЛЬЦА" in guard_system and instructions.MEDIA_CONTROL not in guard_system
    assert guard_system.endswith("ГРАНИЦА ВЛАДЕЛЬЦА " + instructions.GUARD_IDENTITY_RULE)
    assert fake_llm.gen_calls[0][0][0]["content"].endswith("ГРАНИЦА ВЛАДЕЛЬЦА")


async def test_the_settings_api_lists_every_instruction_and_nothing_fixed(api, world):
    csrf = await login(api, OWNER[0])
    body = (await api.get("/api/settings")).json()
    assert "fixed" not in body
    items = {item["key"]: item for item in body["items"]}
    for key, default in instructions.DEFAULTS.items():
        assert items[key]["value"] == default and items[key]["is_default"], key
    saved = await api.put("/api/settings/guard_control", json={"value": "ПРАВИЛО"},
                          headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200 and saved.json()["value"] == "ПРАВИЛО"
    assert "ПРАВИЛО" in guard_system_prompt("П")
