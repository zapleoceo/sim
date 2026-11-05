"""`python -m sim.seed`: idempotent initial data (docs/persona-prompts.md) and the superadmins from SIM_SUPERADMIN_TELEGRAM_IDS.

Existing rows are matched by name; only an EMPTY persona description / prohibitions (and the
untouched old disclosure default) are filled in, so edits made in the admin survive a re-run."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Persona, Strategy
from sim.adapters.db.repositories.catalog import PersonaRepo, StrategyRepo
from sim.adapters.db.session import create_all, transaction
from sim.config import Settings, parse_telegram_ids, settings
from sim.persona_prompts import (
    EVA,
    MARINA,
    PERSONA_TEXTS,
    STAGE_PLAYBOOK,
    STRATEGY_GOAL,
    STRATEGY_NAME,
    STRATEGY_PROMPT,
    STRATEGY_RULES,
)
from sim.wiring import Container, build_container

log = logging.getLogger(__name__)

STRATEGY: dict[str, Any] = {
    "name": STRATEGY_NAME,
    "partner_name": "RCIPM",
    "goal": STRATEGY_GOAL,
    "prompt": STRATEGY_PROMPT,
    "offer": (
        "RCIPM (rcipm.com): «Стратегическое мышление — навык будущего. Образовательная среда, "
        "где решения становятся осознанными и системными». Других деталей продукта, цен и "
        "условий в этом описании нет: если клиент спрашивает — честно скажи, что уточнишь."
    ),
    "registration_url": "https://rcipm.com/?ref={ref}",
    "stage_playbook": STAGE_PLAYBOOK,
    "rules": STRATEGY_RULES,
    "followup_hours": [24, 72, 168],
    "reply_delay_s": [20, 90],
    "notes": (
        "TO CONFIRM with the owner: точная ссылка регистрации (шаблон https://rcipm.com/?ref={ref} "
        "— предположение, сайт закрыт проверкой «я не робот»), описание продукта и цены."
    ),
}

# Said only when the client directly asks what she is (the prompt tells her to stay in
# character otherwise and never to volunteer it).
DISCLOSURE = (
    "Я виртуальная помощница проекта, но всё, что рассказываю, — по делу 🙂"
)
OLD_DISCLOSURE = (
    "Я виртуальный ассистент проекта (ИИ), а не живой человек. Если вы хотите поговорить с "
    "человеком — скажите, и я передам диалог оператору."
)

PERSONAS: list[dict[str, Any]] = [
    {
        "name": MARINA,
        "age": 45,
        "gender": "female",
        "language": "русский (переходит на украинский, если клиент пишет по-украински)",
        **PERSONA_TEXTS[MARINA],
    },
    {
        "name": EVA,
        "age": 39,
        "gender": "female",
        "language": "русский и украинский (отвечает на языке клиента)",
        **PERSONA_TEXTS[EVA],
    },
]


async def seed_catalog(session: AsyncSession) -> dict[str, int]:
    strategies, personas = StrategyRepo(session), PersonaRepo(session)
    created = {"strategies": 0, "personas": 0}
    strategy = await strategies.by_name(STRATEGY_NAME)
    if strategy is None:
        strategy = await strategies.add(Strategy(**STRATEGY))
        created["strategies"] += 1
    elif not strategy.prompt.strip():
        strategy.prompt = STRATEGY_PROMPT   # added in 0005: fill once, owner edits win after that
    for data in PERSONAS:
        existing = await personas.by_name(data["name"])
        if existing is not None:
            # Owner edits win: only fill what is still empty (or the untouched old default).
            for column in ("description", "prohibitions"):
                if not getattr(existing, column).strip():
                    setattr(existing, column, data[column])
            if existing.disclosure_text.strip() == OLD_DISCLOSURE:
                existing.disclosure_text = DISCLOSURE
        else:
            await personas.add(
                Persona(
                    **data,
                    model="",  # empty = Broker chain chat:sales
                    strategy_id=strategy.id,
                    disclosure_text=DISCLOSURE,
                    is_virtual_disclosed=True,
                    is_active=True,
                )
            )
            created["personas"] += 1
    return created


async def seed(container: Container, cfg: Settings) -> dict[str, Any]:
    if cfg.env != "prod" and cfg.database_url.startswith("sqlite"):
        await create_all(container.engine)  # dev convenience; prod schema is Alembic's
    async with transaction(container.factory) as s:
        created: dict[str, Any] = dict(await seed_catalog(s))
        created["superadmins"] = await container.auth(s).sync_superadmins(
            parse_telegram_ids(cfg.superadmin_telegram_ids)
        )
    return created


async def main() -> None:
    cfg = settings()
    container = build_container(cfg)
    try:
        log.info("seed: %s", await seed(container, cfg))
    finally:
        await container.engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main())
