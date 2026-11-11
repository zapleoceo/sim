#!/usr/bin/python3
"""CI gate on an empty disposable PostgreSQL service database.

Run with SIM_PG_DISPOSABLE=YES and SIM_PG_TEST_URL pointing to a dedicated database
whose name ends in `_sim_release_test`. Refuses existing application tables.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))
from sim.adapters.db.repositories.jobs import JobRepo  # noqa: E402
from sim.adapters.db.session import make_session_factory, transaction  # noqa: E402
from sim.clock import utcnow  # noqa: E402
from sim.services.send_receipts import SendReceipts, SendUncertain  # noqa: E402
from sim.services.tracing import Tracer  # noqa: E402


def require_disposable():
    url = os.environ.get("SIM_PG_TEST_URL", "")
    parsed = make_url(url) if url else None
    if (os.environ.get("SIM_PG_DISPOSABLE") != "YES" or parsed is None or
            parsed.drivername != "postgresql+asyncpg" or
            not (parsed.database or "").endswith("_sim_release_test") or
            os.environ.get("SIM_ENV", "").lower() == "prod"):
        raise SystemExit("requires explicit empty disposable PostgreSQL test DB")
    return url


async def require_empty(url):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            tables = (await conn.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            ))).scalar_one()
            if tables:
                raise SystemExit("test DB has tables; refusing migration")
    finally:
        await engine.dispose()


async def schema_and_concurrency(url):
    engine = create_async_engine(url, pool_size=5, max_overflow=5)
    factory = make_session_factory(engine)
    try:
        async with engine.connect() as conn:
            columns = set((await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='message'"
            ))).scalars())
            if not {"operator_id", "operator_name"} <= columns:
                raise AssertionError("0008 operator columns missing")
            indexes = set((await conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='message'"
            ))).scalars())
            if not {"ix_message_created_at_id", "ix_message_dialog_created_at_id"} <= indexes:
                raise AssertionError("0008 message indexes missing")
            if not (await conn.execute(text("SELECT to_regclass('public.outbound_send_intent')"))).scalar_one():
                raise AssertionError("0007 outbound intent table missing")

        # Real PostgreSQL unique-key contention for durable intent, no external transport.
        receipts = SendReceipts(factory, utcnow)
        results = await asyncio.gather(
            receipts.claim(910000002, "a" * 64),
            receipts.claim(910000002, "a" * 64),
            return_exceptions=True,
        )
        if sum(result is None for result in results) != 1 or sum(isinstance(result, SendUncertain) for result in results) != 1:
            raise AssertionError(f"unexpected send-intent contention: {results!r}")

        # Real PostgreSQL SKIP LOCKED and generation fence, no worker process or send.
        now = utcnow()
        async with transaction(factory) as session:
            job = await JobRepo(session).enqueue("release_smoke", {}, now)
            job_id = job.id
        async def claim_one():
            async with transaction(factory) as session:
                jobs = await JobRepo(session).claim(now, 60, 1)
                return [(item.id, item.attempts) for item in jobs]
        claims = await asyncio.gather(claim_one(), claim_one())
        if sorted(map(len, claims)) != [0, 1]:
            raise AssertionError(f"unexpected job claims: {claims!r}")
        winner = next(x[0] for x in claims if x)
        if winner != (job_id, 1):
            raise AssertionError(f"wrong claim generation: {winner!r}")
        async with transaction(factory) as session:
            repo = JobRepo(session)
            if await repo.complete(job_id, now, claim_attempt=0) is not False:
                raise AssertionError("stale claim completed a job")
            if await repo.complete(job_id, now, claim_attempt=1) is not True:
                raise AssertionError("current claim could not complete a job")
        print("PostgreSQL schema, send-intent contention, SKIP LOCKED and claim fence PASS")
    finally:
        await engine.dispose()


async def check_state(url, expected_revision):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if revision != expected_revision:
                raise AssertionError(f"migration revision {revision!r} != {expected_revision!r}")
            count = (await conn.execute(text(
                "SELECT count(*) FROM outbound_send_intent WHERE message_id=910000002"
            ))).scalar_one()
            if count != 1:
                raise AssertionError("outbound intent history was lost")
            columns = set((await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='message'"
            ))).scalars())
            indexes = set((await conn.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='message'"
            ))).scalars())
            operator = {"operator_id", "operator_name"}
            ordering = {"ix_message_created_at_id", "ix_message_dialog_created_at_id"}
            if expected_revision == "0008":
                if not operator <= columns or not ordering <= indexes:
                    raise AssertionError("0008 schema missing after downgrade refusal")
            elif expected_revision == "0007":
                if operator & columns or ordering & indexes:
                    raise AssertionError("0008 schema remained after 0008 downgrade")
            else:
                raise AssertionError("unexpected revision requested")
    finally:
        await engine.dispose()


def require_intent_downgrade_refusal(downgrade):
    """Only the migration's explicit history guard counts as a successful refusal."""
    try:
        downgrade()
    except RuntimeError as exc:
        if str(exc) != "Cannot discard outbound send intents; retain delivery history":
            raise
        print("0007 nonempty downgrade blocked by explicit intent-history guard")
    else:
        raise AssertionError("0007 downgrade unexpectedly discarded an outbound intent")


def main():
    url = require_disposable()
    asyncio.run(require_empty(url))
    os.environ["SIM_DATABASE_URL"] = url
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.config_file_name = None
    command.upgrade(cfg, "0008")
    asyncio.run(schema_and_concurrency(url))
    # Disposable DB only: 0008 schema downgrade loses operator data. Production must not do this.
    command.downgrade(cfg, "0007")
    asyncio.run(check_state(url, "0007"))
    command.upgrade(cfg, "0008")
    require_intent_downgrade_refusal(lambda: command.downgrade(cfg, "0006"))
    # PostgreSQL transactional DDL should roll back the whole failed 0008→0006 attempt.
    # A partial downgrade or missing intent is a failed gate, even with the right error.
    asyncio.run(check_state(url, "0008"))
    print("PostgreSQL migration 0007/0008 disposable smoke PASS")
    command.upgrade(cfg, "head")
    asyncio.run(require_app_setting_table(url, present=True))
    asyncio.run(trace_log_roundtrip(url))
    command.downgrade(cfg, "0008")
    asyncio.run(require_app_setting_table(url, present=False))
    command.upgrade(cfg, "head")
    print("PostgreSQL migration 0009 app_setting round trip PASS")
    print("PostgreSQL trace log migration and purge PASS")


async def require_app_setting_table(url, *, present):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            found = (await conn.execute(text("SELECT to_regclass('public.app_setting')"))).scalar_one()
            if bool(found) != present:
                raise AssertionError(f"app_setting present={bool(found)}, expected {present}")
    finally:
        await engine.dispose()


async def trace_log_roundtrip(url):
    """The trace log on real PostgreSQL: BIGINT ids, JSON detail, batched retention purge."""
    engine = create_async_engine(url)
    try:
        now = datetime(2026, 11, 11, 12)
        async with engine.begin() as conn:
            for days in (40, 31, 1):
                await conn.execute(text(
                    "INSERT INTO trace_event (trace_id, kind, name, status, started_at, actor, detail) "
                    "VALUES ('t', 'http', 'GET /api/x', 'ok', :at, '', CAST(:detail AS JSON))"
                ), {"at": now - timedelta(days=days), "detail": '{"path": "/api/x"}'})
        tracer = Tracer(make_session_factory(engine), clock=lambda: now)
        deleted = await tracer.purge(30, batch=1)
        if deleted != 2:
            raise AssertionError(f"trace purge deleted {deleted} rows, expected 2")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
