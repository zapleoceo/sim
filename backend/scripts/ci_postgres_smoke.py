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
from worker_recovery_smoke import worker_recovery_checks  # noqa: E402

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
        async with transaction(factory) as session:
            recovery = await JobRepo(session).enqueue("recovery_smoke", {}, now)
            recovery_id = recovery.id
        async with transaction(factory) as session:
            [first] = await JobRepo(session).claim(now, 90, 1)
            assert first.id == recovery_id
        async with factory() as owner:
            assert await JobRepo(owner).lock_attempt(recovery_id, 1) is not None
            async with transaction(factory) as competitor:
                assert await JobRepo(competitor).claim(now + timedelta(seconds=91), 90, 1) == []
                assert await JobRepo(competitor).lock_attempt(recovery_id, 1) is None
            await owner.rollback()  # process loss also releases transaction locks
        async with transaction(factory) as session:
            [second] = await JobRepo(session).claim(now + timedelta(seconds=91), 90, 1)
            assert second.id == recovery_id and second.attempts == 2
        async with transaction(factory) as session:
            repo = JobRepo(session)
            assert await repo.lock_attempt(recovery_id, 1) is None
            assert await repo.lock_attempt(recovery_id, 2) is not None
            assert await repo.complete(recovery_id, now, claim_attempt=2)
        print("PostgreSQL schema, send-intent contention, execution lock and restart fence PASS")
        await worker_recovery_checks(factory)
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
    # The concurrency gate drives the current ORM (JobRepo), so it needs the head schema.
    command.upgrade(cfg, "head")
    asyncio.run(schema_and_concurrency(url))
    asyncio.run(reengagement_concurrency(url))
    asyncio.run(content_mcp_concurrency(url))
    asyncio.run(mcp_oauth_concurrency(url))
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


async def reengagement_concurrency(url):
    """Real transaction locks fence simultaneous sweeps and stale delivery eligibility."""
    from sim.adapters.db.locks import NS_DIALOG, lock
    from sim.adapters.db.models import Channel, Client, Dialog, Message, Persona
    from sim.domain.enums import Author, ChannelKind, Delivery, Direction, SessionStatus
    from sim.services.dialog_state import apply_message
    from sim.services.reengagement import ReengagementService, signature

    engine = create_async_engine(url)
    factory = make_session_factory(engine)
    now = datetime(2026, 11, 16, 12)
    try:
        async with transaction(factory) as s:
            p = Persona(name="CI synthetic silence")
            s.add(p)
            await s.flush()
            ch = Channel(persona_id=p.id, kind=ChannelKind.TELEGRAM, name="fake", status=SessionStatus.ACTIVE)
            c = Client(persona_id=p.id, ref_code="silence-ci")
            s.add_all([ch, c])
            await s.flush()
            d = Dialog(channel_id=ch.id, client_id=c.id, external_thread_id="fake",
                       last_message_at=now - timedelta(minutes=6))
            s.add(d)
            await s.flush()
            did = d.id
        async with transaction(factory) as first:
            assert await ReengagementService(first, clock=lambda: now).sweep() == 1
            async with transaction(factory) as second:
                assert await ReengagementService(second, clock=lambda: now).sweep() == 0
        async with transaction(factory) as s:
            d = await s.get(Dialog, did)
            p = await s.get(Persona, d.client.persona_id)
            m = Message(dialog_id=did, direction=Direction.OUT, author=Author.AI,
                        text="synthetic plan", delivery=Delivery.QUEUED)
            s.add(m)
            await s.flush()
            d.reengagement_plan = {"revision": d.activity_revision, "signature": signature(d, p),
                "message_id": m.id, "status": "scheduled", "at": now.isoformat() + "Z"}
            mid, revision = m.id, d.activity_revision
        # An inbound writer owns the exact lock used by final pre-send checking.
        async with transaction(factory) as writer:
            await lock(writer, NS_DIALOG, did)
            d = await writer.get(Dialog, did)
            incoming = Message(dialog_id=did, direction=Direction.IN, author=Author.CLIENT,
                               text="new context", delivery=Delivery.SENT, created_at=now)
            writer.add(incoming)
            await writer.flush()
            apply_message(d, incoming)
            reached = asyncio.Event()

            async def sender():
                async with transaction(factory) as s:
                    reached.set()
                    await lock(s, NS_DIALOG, did)
                    d = await s.get(Dialog, did)
                    m = await s.get(Message, mid)
                    return await ReengagementService(s, clock=lambda: now).send_valid(d, m, revision)

            attempt = asyncio.create_task(sender())
            await reached.wait()
            assert not attempt.done()
        assert await asyncio.wait_for(attempt, timeout=10) is False
        print("PostgreSQL contextual-plan sweep and inbound/send fencing PASS")
    finally:
        await engine.dispose()


async def content_mcp_concurrency(url):
    """Two delegated writers cannot both replace the same content revision."""
    from sqlalchemy import select

    from sim.adapters.db.models import ContentPost, McpAudit, McpGrant, Persona
    from sim.services.content_mcp import ContentMcp, McpError, post_data

    engine = create_async_engine(url)
    factory = make_session_factory(engine)
    try:
        async with transaction(factory) as s:
            persona = Persona(name="Synthetic MCP concurrency")
            s.add(persona)
            await s.flush()
            post = ContentPost(persona_id=persona.id, caption="Initial")
            s.add(post)
            await s.flush()
            pid, expected = post.id, post_data(post)["revision"]

        def delegation():
            return McpGrant(id=900001, scopes=["content:edit"], post_ids=[pid], dialog_ids=[])

        async def edit(caption):
            async with transaction(factory) as s:
                return await ContentMcp(s, delegation(), 900001, utcnow).call("edit_draft_caption", {
                    "post_id": pid, "caption": caption, "expected_revision": expected,
                    "reason": "Synthetic requested edit"})

        results = await asyncio.gather(edit("A"), edit("B"), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert sum(isinstance(r, McpError) for r in results) == 1
        async with factory() as s:
            saved = post_data(await s.get(ContentPost, pid))
            audit_ids = list(await s.scalars(select(McpAudit.id)))
        try:
            async with transaction(factory) as s:
                await ContentMcp(s, delegation(), 900001, utcnow).call("edit_draft_caption", {
                    "post_id": pid, "caption": "Rolled back", "expected_revision": saved["revision"],
                    "reason": "Synthetic rollback"})
                await s.flush()
                raise RuntimeError("synthetic rollback")
        except RuntimeError:
            pass
        async with factory() as s:
            assert post_data(await s.get(ContentPost, pid)) == saved
            assert list(await s.scalars(select(McpAudit.id))) == audit_ids
        print("PostgreSQL MCP revision contention and atomic audit rollback PASS")
    finally:
        await engine.dispose()


async def mcp_oauth_concurrency(url):
    """The same authorization code cannot mint two live grants, even across processes."""
    from sqlalchemy import select

    from sim.adapters.db.models import AdminUser, AuthSession, McpAuthorization, McpGrant
    from sim.config import Settings
    from sim.domain.enums import Role
    from sim.services.auth import hash_token
    from sim.services.mcp_oauth import challenge, exchange

    engine = create_async_engine(url)
    factory = make_session_factory(engine)
    now = datetime(2026, 11, 16, 12)
    cfg = Settings(public_url="https://sim.example", mcp_oauth_enabled=True,
        mcp_oauth_client_id="https://chatgpt.com/oauth/client.json",
        mcp_oauth_redirect_uri="https://chatgpt.com/synthetic_callback")
    verifier = "v" * 64
    data = {"grant_type": "authorization_code", "code": "synthetic-code", "client_id": cfg.mcp_oauth_client_id,
        "redirect_uri": cfg.mcp_oauth_redirect_uri, "resource": cfg.public_url + "/api/mcp", "code_verifier": verifier}
    try:
        async with transaction(factory) as s:
            user = AdminUser(role=Role.OWNER, display_name="Synthetic OAuth owner")
            s.add(user)
            await s.flush()
            session = AuthSession(user_id=user.id, token_hash=hash_token("synthetic-session"),
                csrf_token="synthetic-csrf", expires_at=now + timedelta(hours=1))  # noqa: S106 - disposable fixture
            s.add(session)
            await s.flush()
            row = McpAuthorization(nonce_hash=hash_token("synthetic-nonce"), code_hash=hash_token(data["code"]),
                session_id=session.id, client_id=data["client_id"], redirect_uri=data["redirect_uri"],
                issuer=cfg.public_url, resource=data["resource"], state="synthetic-state", challenge=challenge(verifier),
                scopes=["dialog:read"], dialog_ids=[1], post_ids=[], consented=True, expires_at=now + timedelta(seconds=60))
            s.add(row)
            await s.flush()
            aid = row.id

        async def redeem():
            async with transaction(factory) as s:
                return await exchange(s, cfg, lambda: now, data)

        results = await asyncio.gather(redeem(), redeem())
        assert sum("access_token" in r for r in results) == 1
        assert sum(r.get("error") == "invalid_grant" for r in results) == 1
        async with factory() as s:
            row = await s.get(McpAuthorization, aid)
            grants = list(await s.scalars(select(McpGrant).where(McpGrant.session_id == row.session_id)))
            assert row.consumed and len(grants) == 1 and grants[0].revoked
        print("PostgreSQL OAuth single-use code contention and replay revocation PASS")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
