from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import ContentPost, Persona, Strategy
from sim.adapters.db.session import transaction
from sim.adapters.media import MediaStore
from sim.domain.enums import ContentStatus
from sim.seed import seed
from sim.services.content import NOT_IMPLEMENTED
from sim.services.conversation import persona_profile, strategy_profile
from sim.services.prompt import PersonaProfile, StrategyProfile, stable_system_prompt


def test_secretbox_roundtrip_and_rotation():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    token = SecretBox(old).encrypt_json({"a": 1})
    rotated = SecretBox(f"{new},{old}")
    assert rotated.decrypt_json(token) == {"a": 1}                  # old key still decrypts
    assert SecretBox(new).decrypt_json(rotated.encrypt_json({"b": 2})) == {"b": 2}
    with pytest.raises(ValueError):
        SecretBox(new).decrypt(token)
    assert SecretBox(old).decrypt_json(None) == {}
    with pytest.raises(RuntimeError):
        SecretBox("")


def test_media_store_rejects_path_traversal(tmp_path):
    store = MediaStore(tmp_path)
    name = store.save(b"x", "ogg")
    assert store.path(name) is not None
    for bad in ("../secret.txt", "..%2f", "a/b.ogg", "x.ogg"):
        assert store.path(bad) is None


async def test_seed_is_idempotent_and_complete(container):
    container.settings.superadmin_telegram_ids = "42, junk"
    first = await seed(container, container.settings)
    second = await seed(container, container.settings)
    assert first == {"strategies": 1, "personas": 2, "superadmins": 1}
    assert second == {"strategies": 0, "personas": 0, "superadmins": 0}
    async with container.factory() as s:
        personas = (await s.scalars(select(Persona).order_by(Persona.id))).all()
        strategy = (await s.scalars(select(Strategy))).one()
    assert [p.name for p in personas] == ["Марина Светлова", "Ева Коган"]
    assert strategy.followup_hours == [24, 72, 168]
    assert "TO CONFIRM" in strategy.notes and "{ref}" in strategy.registration_url
    for p in personas:
        assert p.model == "" and p.strategy_id == strategy.id
        system = stable_system_prompt(persona_profile(p), strategy_profile(strategy))
        assert p.name in system and "Сама никогда не говори, что ты ИИ" in system
        assert p.description and p.prohibitions and "Чего нельзя никогда" in system
        assert "виртуальная помощница" not in system and "If asked directly what you are" not in system


async def test_publish_without_publisher_marks_failed(container, world):
    async with transaction(container.factory) as s:
        post = ContentPost(persona_id=world.persona.id, status=ContentStatus.APPROVED)
        s.add(post)
        await s.flush()
        pid = post.id
    async with transaction(container.factory) as s:
        await container.content(s).publish(pid)
    async with container.factory() as s:
        row = await s.get(ContentPost, pid)
        assert row.status is ContentStatus.FAILED and row.error == NOT_IMPLEMENTED


async def test_seed_fills_only_empty_persona_texts(container):
    await seed(container, container.settings)
    async with transaction(container.factory) as s:
        marina, eva = (await s.scalars(select(Persona).order_by(Persona.id))).all()
        marina.description = "Своё описание владельца"
        marina.prohibitions = ""
        eva.description = ""
    await seed(container, container.settings)
    async with container.factory() as s:
        marina, eva = (await s.scalars(select(Persona).order_by(Persona.id))).all()
    assert marina.description == "Своё описание владельца"     # owner edit survives
    assert "не давить и не торопить" in marina.prohibitions            # empty -> filled
    assert eva.description.startswith("Я Ева, мне 39") and "национальных стереотипов" in eva.prohibitions
    assert "не обесценивать трудности" in marina.prohibitions


async def test_apply_persona_prompts_overwrites_seeded_texts(container):
    from sim.persona_prompts import MARINA, STRATEGY_PROMPT, apply_persona_prompts

    await seed(container, container.settings)
    async with transaction(container.factory) as s:
        marina = (await s.scalars(select(Persona).where(Persona.name == MARINA))).one()
        marina.description = "правки владельца"
        strategy = (await s.scalars(select(Strategy))).one()
        strategy.prompt = ""
        strategy.rules = ["старое"]
    async with transaction(container.factory) as s:
        changes, missing = await apply_persona_prompts(s)
    assert missing == []
    assert {(c.target, c.field) for c in changes} >= {(MARINA, "description"), (f"strategy «{strategy.name}»", "prompt")}
    async with container.factory() as s:
        marina = (await s.scalars(select(Persona).where(Persona.name == MARINA))).one()
        strategy = (await s.scalars(select(Strategy))).one()
    assert marina.description.startswith("Мне 45, я Марина") and strategy.prompt == STRATEGY_PROMPT
    assert strategy.goal.startswith("Твоя цель — подружиться") and len(strategy.rules) == 6
    async with transaction(container.factory) as s:
        again, _ = await apply_persona_prompts(s)
    assert again == []                                  # idempotent


def test_prompt_template_is_free_and_keeps_the_honesty_floor():
    from sim.persona_prompts import STRATEGY_PROMPT

    system = stable_system_prompt(
        PersonaProfile(name="Х", description="d"), StrategyProfile(prompt=STRATEGY_PROMPT, rules=["r"])
    )
    assert "ориентиры, а не скрипт" in system and "Сама никогда не говори, что ты ИИ" in system
    assert "никогда не утверждай" in system and "виртуальная помощница" not in system
    assert "милениал" in system and "не используй длинные тире" in system
    assert "ОРИЕНТИРЫ СТРАТЕГИИ" in system and "Сначала человек, потом предложение" in system
    assert system.index("ОРИЕНТИРЫ СТРАТЕГИИ") < system.index("ПРАВИЛА:")
