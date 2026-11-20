"""Private Studio text queue on fake Broker routes only."""
from __future__ import annotations

from datetime import UTC, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from sim.adapters.db.models import AdminUser, Job, TraceEvent
from sim.adapters.db.session import transaction
from sim.services import studio_chat
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, OWNER, login

MODEL = "claude-sub/claude-sonnet-5-5"
BODY = {"model": MODEL, "messages": [{"role": "user", "content": "studio-private-fixture"}]}


async def start(api, container, fake_llm):
    container.settings.studio_owner_telegram_id = OWNER[0]
    auth = {"X-CSRF-Token": await login(api, *OWNER)}
    millis = int(container.clock().replace(tzinfo=UTC).timestamp() * 1000)

    async def models():
        return [{"id": MODEL, "available": True, "tested": True, "billing_type": "subscription"}]

    fake_llm.list_models = models
    return auth, f"/api/studio/chat-operations/{millis}-{uuid4()}"


async def test_studio_chat_queue_disabled_keeps_media_off(api, container, fake_llm):
    auth, path = await start(api, container, fake_llm)
    assert (await api.post(path, json=BODY, headers=auth)).status_code == 423
    assert (await api.post("/api/studio/media", json={
        "operation_id": str(uuid4()), "kind": "speech", "text": "fixture",
    }, headers=auth)).status_code == 423
    assert not fake_llm.calls


async def test_studio_chat_returns_202_read_only_recovery_and_checks_binding(api, container, fake_llm):
    auth, path = await start(api, container, fake_llm)
    container.settings.studio_chat_queue_enabled = True
    assert (await api.post(path, json=BODY)).status_code == 403
    assert (await api.post(path, json=BODY, headers=auth)).status_code == 202
    assert (await api.post(path, json=BODY, headers=auth)).status_code == 202
    assert (await api.get(path)).json() == {"status": "pending"}
    assert not fake_llm.calls
    fake_llm.replies = ["Studio response"]
    assert await Worker(container).tick() == 1
    result = await api.get(path)
    assert result.json()["result"]["text"] == "Studio response"
    assert result.headers["cache-control"] == "private, no-store"
    assert len(fake_llm.calls) == 1
    async with container.factory() as s:
        rows = (await s.scalars(select(TraceEvent))).all()
        assert all("studio-private-fixture" not in str(row.detail) for row in rows)
        assert all("broker_exchange" not in (row.detail or {}) for row in rows)
    await login(api, *OPERATOR)
    assert (await api.get(path)).status_code == 403


async def test_studio_chat_rechecks_binding_after_catalog_before_paid_call(api, container, fake_llm):
    auth, path = await start(api, container, fake_llm)
    container.settings.studio_chat_queue_enabled = True
    assert (await api.post(path, json=BODY, headers=auth)).status_code == 202

    async def catalog_after_rebind():
        container.settings.studio_owner_telegram_id += 1
        return [{"id": MODEL, "available": True, "tested": True, "billing_type": "subscription"}]

    fake_llm.list_models = catalog_after_rebind
    assert await Worker(container).tick() == 1
    assert not fake_llm.calls
    assert (await api.get(path)).status_code == 403
    container.settings.studio_owner_telegram_id = OWNER[0]
    assert (await api.get(path)).json() == {"status": "unavailable"}


async def test_studio_chat_purge_moves_past_clean_rows_in_two_batches(container):
    now = container.clock()
    async with transaction(container.factory) as s:
        owner = AdminUser(telegram_id=12345, display_name="Synthetic Studio owner")
        s.add(owner)
        jobs = [Job(kind="studio_chat", status="done", run_at=now) for _ in range(401)]
        s.add_all(jobs)
        await s.flush()
        s.add_all([
            studio_chat.StudioChatOperation(
                id=f"{i:04d}", owner_id=owner.id, binding="b" * 64, fingerprint="f" * 64,
                job_id=job.id, expires_at=now - timedelta(minutes=10),
                key_expires_at=now + timedelta(hours=1),
                input_cipher="encrypted-input" if 200 <= i < 400 else None,
                result_cipher="encrypted-result" if i == 400 else None,
            )
            for i, job in enumerate(jobs)
        ])

    async def remaining():
        async with container.factory() as s:
            operation = studio_chat.StudioChatOperation
            return list(await s.scalars(select(operation.id).where(
                operation.input_cipher.is_not(None) | operation.result_cipher.is_not(None),
            ).order_by(operation.id)))

    assert len(await remaining()) == 201
    for expected in (["0400"], []):
        async with transaction(container.factory) as s:
            await studio_chat.purge(s, now)
        assert await remaining() == expected
    async with container.factory() as s:
        assert await s.scalar(select(func.count()).select_from(studio_chat.StudioChatOperation)) == 401
