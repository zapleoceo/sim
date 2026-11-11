"""System trace log: one trace from the webhook to the send, attempts, masking, filters, retention."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from alembic import command
from sqlalchemy import select

from sim.adapters.db.models import Job, LlmCall, TraceEvent
from sim.adapters.db.session import transaction
from sim.api.activity_schemas import trace_status
from sim.ports.llm import LLMError
from sim.services import jobkinds, tracing
from sim.services.tracing import Tracer, mask, trace_scope
from sim.worker.handlers import Handler
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, OWNER, decision_json, login
from tests.test_guard import make_due
from tests.test_migrations import alembic_config, columns

HOOK_HEADERS = {"x-secret": "hook-secret"}


async def events(container) -> list[TraceEvent]:
    async with container.factory() as s:
        return list((await s.scalars(select(TraceEvent).order_by(TraceEvent.id))).all())


def test_mask_redacts_credentials_and_keeps_plain_text():
    text = ("bot 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw failed; Authorization: Bearer abc.def "
            "url https://user:pa55@host/x?token=SECRET&api_key=K1&page=2 password: hunter2")
    masked = mask(text)
    for secret in ("AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", "abc.def", "pa55", "SECRET", "K1", "hunter2"):
        assert secret not in masked
    assert "page=2" in masked and "failed" in masked
    assert mask("plain text " * 200, 10) == "plain text"
    assert mask(None) is None


async def test_webhook_reply_and_send_are_one_trace(api, container, world, fake_llm, connector):
    ok = await api.post(f"/hooks/telegram/{world.channel.id}", headers=HOOK_HEADERS,
                        json={"chat": 55, "user": 7, "text": "Привет", "id": 1, "name": "Боб"})
    assert ok.status_code == 200
    trace_id = ok.headers["X-Trace-Id"]
    first = await events(container)
    assert [(e.kind, e.status) for e in first] == [("inbound", "ok"), ("webhook", "ok")]
    assert all(e.trace_id == trace_id for e in first)
    assert first[1].http_status == 200 and first[1].name == f"POST /hooks/telegram/{world.channel.id}"
    assert first[0].client_id and first[0].dialog_id and first[0].message_id

    async with container.factory() as s:
        reply = (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY))).one()
        assert reply.trace_id == trace_id

    fake_llm.replies = [decision_json(reply="Здравствуйте!")]
    worker = Worker(container)
    await make_due(container)
    await worker.tick()                     # reply: generator + guard, queues the send
    await make_due(container)
    await worker.tick()                     # send
    assert connector.port.sent == [("55", "Здравствуйте!")]

    steps = await events(container)
    assert {e.trace_id for e in steps} == {trace_id}
    kinds = [e.kind for e in steps]
    assert {"llm", "guard", "job", "send"} <= set(kinds)
    reply_step = next(e for e in steps if e.kind == "job" and e.name == jobkinds.REPLY)
    assert reply_step.attempt == 1 and reply_step.detail["outcome"] == "sent"
    assert reply_step.detail["queue_ms"] >= 0 and reply_step.detail["guard"] == "ok"
    async with container.factory() as s:
        assert {c.trace_id for c in (await s.scalars(select(LlmCall))).all()} == {trace_id}

    h = {"X-CSRF-Token": await login(api, *OWNER)}
    body = (await api.get(f"/api/activity/traces/{trace_id}", headers=h)).json()
    assert body["status"] == "ok" and body["trace_id"] == trace_id
    assert body["steps"][0]["offset_ms"] == 0 and len(body["steps"]) == len(steps)
    llm = next(s for s in body["steps"] if s["kind"] == "llm")
    assert llm["llm"]["model"] and llm["client"]["display_name"] == "Боб"
    assert (await api.get("/api/activity/traces/" + "0" * 32, headers=h)).status_code == 404
    assert (await api.get("/api/activity/traces/not-a-trace", headers=h)).status_code == 422


async def test_failed_attempt_is_a_retry_and_a_later_success_settles_the_trace(container):
    calls = {"n": 0}

    async def flaky(c, s, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("broker down token=SECRET")
        return {"outcome": "done"}

    async with transaction(container.factory) as s:
        s.add(Job(kind="x", payload={"dialog_id": 9}, run_at=datetime(2026, 1, 1), trace_id="a" * 32))
    worker = Worker(container, {"x": Handler(flaky)})
    await worker.tick()
    await make_due(container)
    await worker.tick()
    steps = await events(container)
    assert [(e.status, e.attempt) for e in steps] == [("retry", 1), ("ok", 2)]
    assert "SECRET" not in steps[0].error and "broker down" in steps[0].error
    assert steps[0].detail["max_attempts"] == 5 and steps[1].detail["outcome"] == "done"
    assert all(e.trace_id == "a" * 32 and e.dialog_id == 9 and e.actor == "worker" for e in steps)
    assert trace_status(steps) == "ok"


def test_trace_status_takes_the_last_attempt_and_ignores_fixed_inner_errors():
    def ev(i, kind, status, job_id=None):
        return TraceEvent(id=i, trace_id="t", kind=kind, status=status, job_id=job_id)

    assert trace_status([ev(1, "llm", "error", 5), ev(2, "job", "retry", 5), ev(3, "job", "ok", 5)]) == "ok"
    assert trace_status([ev(1, "job", "retry", 5)]) == "retry"
    assert trace_status([ev(1, "http", "ok"), ev(2, "send", "error")]) == "error"
    assert trace_status([ev(1, "inbound", "skipped")]) == "skipped"


async def test_api_requests_name_the_admin_and_log_reads_stay_quiet(api, container):
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    res = await api.get("/api/personas", headers=h, params={"q": "x", "token": "SECRET"})
    assert res.headers["X-Trace-Id"]
    await api.get("/api/activity/log", headers=h)
    await api.get("/api/health")
    http = [e for e in await events(container) if e.kind == "http"]
    personas = next(e for e in http if e.name == "GET /api/personas")
    assert personas.actor == f"admin {OWNER[0]}" and personas.admin_id
    assert "SECRET" not in personas.detail["query"] and "q=x" in personas.detail["query"]
    assert not [e for e in http if e.name.startswith(("GET /api/activity/log", "GET /api/health"))]


async def seed_log(container):
    base = datetime(2026, 11, 10, 12)
    rows = [
        TraceEvent(trace_id="1" * 32, kind="http", name="GET /api/clients", status="ok", started_at=base,
                   duration_ms=30, http_status=200, actor="Дима"),
        TraceEvent(trace_id="2" * 32, kind="llm", name="reply · chat:sales", status="error",
                   started_at=base + timedelta(minutes=1), duration_ms=900, error="LLMError: timeout"),
        TraceEvent(trace_id="2" * 32, kind="job", name="reply", status="retry", attempt=2,
                   started_at=base + timedelta(minutes=2), duration_ms=950, job_id=4),
        TraceEvent(trace_id="3" * 32, kind="send", name="send telegram", status="ok",
                   started_at=base + timedelta(minutes=3), duration_ms=120),
    ]
    async with transaction(container.factory) as s:
        s.add_all(rows)


async def test_log_filters_sorts_and_pages_on_the_server(api, container):
    await seed_log(container)
    h = {"X-CSRF-Token": await login(api, *OWNER)}

    async def get(**params):
        res = await api.get("/api/activity/log", headers=h, params=params)
        assert res.status_code == 200, res.text
        return res.json()

    everything = await get()
    assert everything["total"] == 4 and everything["retention_days"] == 30
    assert [i["kind"] for i in everything["items"]] == ["send", "job", "llm", "http"]
    assert [i["duration_ms"] for i in (await get(sort="duration_ms", order="asc"))["items"]] == [30, 120, 900, 950]
    assert (await get(kind="llm,job"))["total"] == 2
    assert (await get(status="error"))["items"][0]["error"] == "LLMError: timeout"
    assert (await get(has_error="true"))["total"] == 1 and (await get(has_error="false"))["total"] == 3
    assert (await get(q="timeout"))["total"] == 1 and (await get(actor="Дим"))["total"] == 1
    assert (await get(trace_id="2222"))["total"] == 2 and (await get(min_attempt=2))["total"] == 1
    assert (await get(min_ms=100, max_ms=950))["total"] == 3 and (await get(http_status=200))["total"] == 1
    assert (await get(name="%"))["total"] == 0                       # LIKE wildcards are literal
    assert (await get(**{"from": "2026-11-10T12:01:30Z", "to": "2026-11-10T12:02:30Z"}))["total"] == 1
    page = await get(size=3, page=2)
    assert page["total"] == 4 and [i["kind"] for i in page["items"]] == ["http"]
    for bad in ({"kind": "nope"}, {"status": "x"}, {"sort": "password"}, {"min_ms": 5, "max_ms": 1}, {"size": 500}):
        assert (await api.get("/api/activity/log", headers=h, params=bad)).status_code == 422


async def test_log_is_owner_only(api, container):
    await seed_log(container)
    h = {"X-CSRF-Token": await login(api, *OPERATOR)}
    assert (await api.get("/api/activity/log", headers=h)).status_code == 403
    assert (await api.get("/api/activity/traces/" + "1" * 32, headers=h)).status_code == 403


async def test_scope_buffers_until_the_unit_of_work_ends(container):
    tracer = Tracer(container.factory, container.clock)
    async with tracer.scope(actor="test", dialog_id=3) as ctx:
        async with tracer.step(tracing.SEND, "send x"):
            pass
        assert len(ctx.pending) == 1 and await events(container) == []
    [step] = await events(container)
    assert step.trace_id == ctx.trace_id and step.actor == "test" and step.dialog_id == 3
    with trace_scope("f" * 32) as outer:
        await tracer.record(tracing.HTTP, "GET /x")
    assert len(outer.pending) == 1          # a bare scope is flushed by its owner


async def test_purge_keeps_the_retention_window(container):
    now = datetime(2026, 11, 11, 12)
    async with transaction(container.factory) as s:
        s.add_all([TraceEvent(trace_id="t", kind="http", name="x", started_at=now - timedelta(days=d))
                   for d in (40, 31, 29, 1)])
    tracer = Tracer(container.factory, clock=lambda: now)
    assert await tracer.purge(0) == 0
    assert await tracer.purge(30, batch=1) == 2
    assert sorted((now - e.started_at).days for e in await events(container)) == [1, 29]


def test_0010_roundtrip(tmp_path):
    db = tmp_path / "trace.db"
    cfg = alembic_config(db)
    cfg.config_file_name = None
    command.upgrade(cfg, "head")
    assert {"trace_id", "kind", "status", "duration_ms", "attempt", "detail", "llm_call_id"} <= columns(db, "trace_event")
    assert "trace_id" in columns(db, "job") and "trace_id" in columns(db, "llm_call")
    command.downgrade(cfg, "0008")
    assert not columns(db, "trace_event") and "trace_id" not in columns(db, "job")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as con:
        indexes = {row[1] for row in con.execute("PRAGMA index_list(trace_event)")}
    assert {"ix_trace_event_started_at_id", "ix_trace_event_trace_started"} <= indexes
