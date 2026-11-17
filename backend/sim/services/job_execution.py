"""Fail closed before external effects if a worker's execution transaction is lost."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Job


class ExecutionLost(RuntimeError):
    """The handler must stop; a reclaimed generation may already own this job."""


@dataclass
class Execution:
    session: AsyncSession
    job_id: int
    generation: int
    transaction: Any
    broker_call_index: int = 0


_execution: ContextVar[Execution | None] = ContextVar("job_execution", default=None)


def next_broker_call() -> tuple[Execution, int] | None:
    """Stable call position within a replayed handler; never an external job identity."""
    owner = _execution.get()
    if owner is None:
        return None
    index = owner.broker_call_index
    owner.broker_call_index += 1
    return owner, index


@contextmanager
def execution_scope(session: AsyncSession, job_id: int, generation: int) -> Iterator[None]:
    token = _execution.set(Execution(
        session, job_id, generation, session.sync_session.get_transaction(),
    ))
    try:
        yield
    finally:
        _execution.reset(token)


async def require_live_execution() -> None:
    """Use the SAME connection holding the row lock, never a fresh health-check session.

    A broken connection/transaction raises before the next external request. There
    is inevitably a race after this check: durable send intents still fence an
    already in-flight transport request and forbid a second send on recovery.
    Interactive operations outside a worker keep their existing authorization.
    """
    owner = _execution.get()
    if owner is None:
        return
    if owner.session.sync_session.get_transaction() is not owner.transaction:
        raise ExecutionLost("execution transaction changed")
    current = await owner.session.scalar(select(Job.id).where(
        Job.id == owner.job_id, Job.status == "running", Job.attempts == owner.generation,
    ))
    if current is None:
        raise ExecutionLost("execution generation changed")
