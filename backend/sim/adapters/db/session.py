"""Engine / session factory."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sim.adapters.db.base import Base


def make_engine(database_url: str) -> AsyncEngine:
    kwargs: dict[str, object] = {}
    if not database_url.startswith("sqlite"):
        kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True)
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def create_all(engine: AsyncEngine) -> None:
    """Dev/test only. Production schema comes from Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
