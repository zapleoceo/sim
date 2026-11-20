"""Fake model/voice coverage of the private asynchronous preview route."""
from __future__ import annotations

from datetime import UTC, timedelta
from uuid import uuid4

from sqlalchemy import select

from sim.adapters.db.models import Job, Persona, PreviewOperation
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.domain.enums import VoiceMode
from sim.ports.llm import LLMError
from sim.services import preview_operations
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, OWNER, decision_json, login
from tests.test_voice import FakeEncoder, FakeSpeech


def operation_url(persona_id, clock):
    millis = int(clock().replace(tzinfo=UTC).timestamp() * 1000)
    return f"/api/personas/{persona_id}/preview-operations/{millis}-{uuid4()}"


async def start(api, container, world, *, enabled=True):
    container.settings.preview_async_enabled = enabled
    auth = {"X-CSRF-Token": await login(api, *OWNER)}
    return auth, operation_url(world.persona.id, container.clock)


async def test_preview_queue_defaults_off(api, container, world):
    auth, path = await start(api, container, world, enabled=False)
    assert (await api.post(path, json={"message": "Привет"}, headers=auth)).status_code == 404


async def test_preview_operation_retry_and_private_text_without_tts(api, container, world, fake_llm):
    auth, path = await start(api, container, world)
    speech, encoder = FakeSpeech(), FakeEncoder()
    container.speech, container.encoder = speech, encoder
    async with transaction(container.factory) as session:
        persona = await session.get(Persona, world.persona.id)
        persona.voice_mode, persona.voice_id = VoiceMode.ALWAYS, "Kore"
    fake_llm.replies = [decision_json(reply="Здравствуйте!", stage="engaged")]
    body = {"message": "Привет", "history": [{"role": "assistant", "text": "Алло"}]}
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    assert (await api.post(path, json={"message": "Другое"}, headers=auth)).status_code == 409
    rejected = await api.post(operation_url(world.persona.id, container.clock), json=body, headers=auth)
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == {"status": "queue_full", "scope": "owner"}
    assert (await api.get(path)).json() == {"status": "pending"}
    assert not fake_llm.calls
    async with container.factory() as session:
        row = await session.scalar(select(PreviewOperation))
        job = await session.get(Job, row.job_id)
        assert "message" not in job.payload and row.input_cipher and not row.result_cipher
    assert await Worker(container).tick() == 1
    done = (await api.get(path)).json()
    assert done["status"] == "complete"
    assert done["message"] == "Привет" and done["history"] == body["history"]
    assert done["result"]["decision"]["stage"] == "engaged"
    assert done["result"]["voice"] is None
    assert not speech.calls and not encoder.seen
    container.settings.preview_async_enabled = False
    recovered = await api.get(path)
    assert recovered.json() == done and recovered.headers["cache-control"] == "no-store"
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    assert (await api.get(path.replace(f"/{world.persona.id}/", "/999999/"))).status_code == 404
    await login(api, *OPERATOR)
    assert (await api.get(path)).status_code == 404
    assert await Worker(container).tick() == 0


async def test_preview_worker_loss_never_reclaims_or_bills(api, container, world, fake_llm):
    auth, path = await start(api, container, world)
    body = {"message": "Привет"}
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    async with transaction(container.factory) as session:
        claimed = await JobRepo(session).claim_preview(container.clock(), 90)
        claimed.locked_until = container.clock() - timedelta(seconds=1)
    assert await Worker(container).tick() == 0
    assert (await api.get(path)).json() == {"status": "unknown"}
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    assert not fake_llm.calls
    async with transaction(container.factory) as session:
        await preview_operations.purge(session, container.clock() + timedelta(hours=2))
    async with container.factory() as session:
        row = await session.scalar(select(PreviewOperation))
        assert not row.input_cipher and not row.result_cipher


async def test_preview_config_edit_cancels_before_model_call(api, container, world, fake_llm):
    auth, path = await start(api, container, world)
    assert (await api.post(path, json={"message": "Привет"}, headers=auth)).status_code == 202
    async with transaction(container.factory) as session:
        persona = await session.get(Persona, world.persona.id)
        persona.agent_prompt = "Изменённый промпт"
    assert await Worker(container).tick() == 1
    assert (await api.get(path)).json() == {"status": "unavailable"}
    assert not fake_llm.calls


async def test_preview_unknown_model_outcome_is_terminal(api, container, world, fake_llm):
    auth, path = await start(api, container, world)
    body = {"message": "Привет"}
    fake_llm.replies = [LLMError("Synthetic provider lost its acknowledgement")]
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    worker = Worker(container)
    assert await worker.tick() == 1
    assert (await api.get(path)).json() == {"status": "unknown"}
    assert (await api.post(path, json=body, headers=auth)).status_code == 202
    assert await worker.tick() == 0
    assert len(fake_llm.calls) == 1


async def test_preview_cleans_pending_when_disabled(api, container, world, fake_llm):
    auth, path = await start(api, container, world)
    assert (await api.post(path, json={"message": "Привет"}, headers=auth)).status_code == 202
    container.settings.preview_async_enabled = False
    worker = Worker(container)
    assert await worker.tick() == 0
    await worker.cleanup_previews()
    assert (await api.get(path)).json() == {"status": "unavailable"}
    async with container.factory() as session:
        row = await session.scalar(select(PreviewOperation))
        assert row.input_cipher is None
    assert not fake_llm.calls


async def test_preview_absence_only_terminal_after_admission_window(api, container, world):
    auth, _ = await start(api, container, world)
    missing = operation_url(world.persona.id, container.clock)
    assert (await api.get(missing)).status_code == 404
    old = operation_url(world.persona.id, lambda: container.clock() - timedelta(minutes=3))
    assert (await api.get(old)).json() == {"status": "expired"}
    assert (await api.post(old, json={"message": "Привет"}, headers=auth)).status_code == 422
