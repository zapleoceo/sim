"""Initial data, run after every migration (`python -m sim.seed`, the `migrate` service).

Idempotent:
- every id from SIM_SUPERADMIN_TELEGRAM_IDS becomes an active admin (added or re-activated);
- if there is no persona at all, one named «Марина» is created with default settings
  (the owner fills the prompt and models on the persona page, or scripts/import_v1.py does)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.config import Settings, get_settings
from sim.db.models import AdminUser, Persona
from sim.db.session import make_engine, make_sessionmaker, transaction

log = logging.getLogger(__name__)

DEFAULT_PERSONA_NAME = "Марина"


@dataclass(frozen=True)
class SeedReport:
    admins_added: int = 0
    admins_reactivated: int = 0
    persona_created: bool = False


async def seed(session: AsyncSession, superadmin_ids: Sequence[int]) -> SeedReport:
    """Applies the seed inside the caller's transaction (flushes, never commits)."""
    added = reactivated = 0
    for telegram_id in dict.fromkeys(superadmin_ids):
        admin = await session.scalar(select(AdminUser).where(AdminUser.telegram_id == telegram_id))
        if admin is None:
            session.add(AdminUser(telegram_id=telegram_id, name="", is_active=True))
            added += 1
        elif not admin.is_active:
            admin.is_active = True
            reactivated += 1
    created = not await session.scalar(select(func.count()).select_from(Persona))
    if created:
        session.add(Persona(name=DEFAULT_PERSONA_NAME))
    await session.flush()
    return SeedReport(admins_added=added, admins_reactivated=reactivated, persona_created=created)


async def run(settings: Settings) -> SeedReport:
    engine = make_engine(settings.database_url, pool_size=1)
    try:
        async with transaction(make_sessionmaker(engine)) as session:
            return await seed(session, settings.superadmin_ids)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    report = asyncio.run(run(get_settings()))
    log.info("seed: %d admins added, %d re-activated, persona created: %s",
             report.admins_added, report.admins_reactivated, report.persona_created)


if __name__ == "__main__":
    main()
