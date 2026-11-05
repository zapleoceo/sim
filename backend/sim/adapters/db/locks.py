"""Per-dialog locks that live exactly as long as the surrounding transaction.

Postgres: advisory transaction locks (`pg_advisory_xact_lock` / `pg_try_advisory_xact_lock`),
released automatically on commit or rollback. SQLite (dev / tests, a single process): the
try-lock is emulated with an in-process set released when the session's transaction ends, and
the blocking lock is a no-op because SQLite already serialises writers.

Two namespaces:
  REPLY   held for a whole reply job (the model call takes minutes): at most one reply is
          being produced per dialog; a second job gets False and retries later.
  DIALOG  held briefly: by the reply job while it re-checks the dialog and queues its message,
          and by ingest while it inserts a client message. Neither can interleave with the
          other, so "no newer client message" stays true until the reply is committed.
"""
from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

NS_REPLY = 1
NS_DIALOG = 2

held: set[tuple[int, int]] = set()   # sqlite emulation only


def is_postgres(session: AsyncSession) -> bool:
    return session.sync_session.get_bind().dialect.name == "postgresql"


async def try_lock(session: AsyncSession, namespace: int, key: int) -> bool:
    """Take the lock if free (non-blocking). Held until the transaction ends."""
    if is_postgres(session):
        got = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"), {"ns": namespace, "key": key}
        )
        return bool(got)
    token = (namespace, key)
    if token in held:
        return False
    held.add(token)

    released = False

    def release(_: Session, transaction: SessionTransaction) -> None:
        nonlocal released
        # One-shot (the listener stays registered: it cannot be removed while firing).
        if not released and transaction.parent is None:  # the outermost transaction ended
            released = True
            held.discard(token)

    event.listen(session.sync_session, "after_transaction_end", release)
    return True


async def lock(session: AsyncSession, namespace: int, key: int) -> None:
    """Wait for the lock, then hold it until the transaction ends."""
    if is_postgres(session):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"), {"ns": namespace, "key": key}
        )
