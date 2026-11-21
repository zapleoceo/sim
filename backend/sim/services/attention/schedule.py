"""The one pending phone check of a persona (docs/attention.md, «Одна проверка»):
`persona.attention_check_at` says when she next looks at her phone, and the task
`agent.phone_check(persona_id)` is deferred for that moment.

- `request_check`: a new notification asks for a check at a sampled time; it wins only when it
  is earlier than the pending one (or the pending one was lost). Atomic UPDATE, no lock.
- `claim`: the task takes a due check by replacing it with a lease (a check that crashed
  half-way comes back when the lease runs out); a job whose check was moved is a no-op.
- `release`: the finished check gives the lease back, unless a newer request replaced it.
- `lost_checks`: overdue checks the sweep (`agent.attention_tick`) defers again.

The caller defers the task AFTER its transaction commits (`queue_check`)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona
from sim.domain.attention import attention_settings
from sim.services.attention.timing import Availability, availability, check_delay
from sim.services.life.store import load_life
from sim.services.queue import TaskQueue

DUE_SLACK = timedelta(seconds=1)            # a job may start a moment before its time
LEASE = timedelta(minutes=10)               # a running check holds the slot this long
LOST_AFTER = timedelta(minutes=2)           # overdue this long = its job was lost


async def request_check(session: AsyncSession, persona_id: int, due: datetime,
                        now: datetime) -> bool:
    """Ask for a check at `due`. True = this is now THE pending check (defer the task after
    commit); False = an earlier one is already pending and covers it."""
    found = (await session.execute(
        update(Persona)
        .where(Persona.id == persona_id,
               or_(Persona.attention_check_at.is_(None), Persona.attention_check_at > due,
                   Persona.attention_check_at < now - LOST_AFTER))
        .values(attention_check_at=due)
        .returning(Persona.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    return found is not None


async def claim(session: AsyncSession, persona_id: int, now: datetime) -> datetime | None:
    """Take the due check: returns the lease (its new `attention_check_at`), or None when no
    check is due (it was moved or already taken: this job is stale)."""
    lease = now + LEASE
    found = (await session.execute(
        update(Persona)
        .where(Persona.id == persona_id, Persona.attention_check_at.is_not(None),
               Persona.attention_check_at <= now + DUE_SLACK)
        .values(attention_check_at=lease)
        .returning(Persona.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    return lease if found is not None else None


async def release(session: AsyncSession, persona_id: int, lease: datetime) -> None:
    await session.execute(
        update(Persona)
        .where(Persona.id == persona_id, Persona.attention_check_at == lease)
        .values(attention_check_at=None)
        .execution_options(synchronize_session=False))


async def lost_checks(session: AsyncSession, now: datetime) -> list[int]:
    return list(await session.scalars(
        select(Persona.id).where(Persona.attention_check_at < now - LOST_AFTER)
        .order_by(Persona.id)))


async def check_after_notice(session: AsyncSession, persona: Persona, now: datetime,
                             u: float, *, av: Availability | None = None) -> datetime | None:
    """A notification came: ask for a check when her life lets her look (sampled with `u`).
    Returns the due time to defer, or None when an earlier check covers it."""
    settings = attention_settings(persona.attention)
    if av is None:
        av = availability(await load_life(session, persona, now), settings)
    due = now + timedelta(seconds=check_delay(av, now, settings.pace, u))
    return due if await request_check(session, persona.id, due, now) else None


async def queue_check(tasks: TaskQueue, persona_id: int, due: datetime | None,
                      now: datetime) -> None:
    """After commit: defer the task for a check `request_check` accepted."""
    if due is not None:
        await tasks.phone_check(persona_id, max(0.0, (due - now).total_seconds()))
