"""sim.seed: superadmins and the first persona, idempotent."""
from __future__ import annotations

from sqlalchemy import select

from sim.db.models import AdminUser, Persona
from sim.seed import DEFAULT_PERSONA_NAME, seed


async def test_first_run_creates_admins_and_persona(db):
    report = await seed(db, [169510539, 42, 42])
    assert (report.admins_added, report.admins_reactivated, report.persona_created) == (2, 0, True)
    admins = (await db.scalars(select(AdminUser).order_by(AdminUser.telegram_id))).all()
    assert [(a.telegram_id, a.is_active) for a in admins] == [(42, True), (169510539, True)]
    personas = (await db.scalars(select(Persona))).all()
    assert [p.name for p in personas] == [DEFAULT_PERSONA_NAME]
    assert personas[0].ai_enabled and personas[0].prompt == ""


async def test_second_run_changes_nothing_but_reactivates(db):
    await seed(db, [42])
    admin = await db.scalar(select(AdminUser).where(AdminUser.telegram_id == 42))
    assert admin is not None
    admin.is_active = False
    await db.flush()

    report = await seed(db, [42])

    assert (report.admins_added, report.admins_reactivated, report.persona_created) == (0, 1, False)
    assert admin.is_active
    assert len((await db.scalars(select(Persona))).all()) == 1


async def test_existing_persona_is_kept(db):
    db.add(Persona(name="Марина Светлова"))
    await db.flush()
    report = await seed(db, [])
    assert not report.persona_created
    assert [p.name for p in (await db.scalars(select(Persona))).all()] == ["Марина Светлова"]
