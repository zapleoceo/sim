"""Shared fixtures. Tests run only in CI against a disposable PostgreSQL (SIM_PG_TEST_URL);
the schema is created from the models once per session and every test runs inside a
transaction that is rolled back, so tests never see each other's rows."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sim.clock import FixedClock
from sim.db.models import Base

PG_URL = os.environ.get("SIM_PG_TEST_URL", "")


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    if not PG_URL or os.environ.get("SIM_PG_DISPOSABLE") != "YES":
        pytest.skip("needs a disposable PostgreSQL (SIM_PG_TEST_URL + SIM_PG_DISPOSABLE=YES)")
    eng = create_async_engine(PG_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to an outer transaction that is rolled back after the test.
    Code under test may call session.commit(): it commits a SAVEPOINT only."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False,
                               join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 21, 18, 30, tzinfo=UTC))
