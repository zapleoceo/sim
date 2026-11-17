"""Synthetic queue integration: never a real connector, subscription or paid API."""
from unittest.mock import AsyncMock

import httpx
import pytest

from sim.adapters.bounded_media import BoundedMediaBuffer
from sim.adapters.db.models import Dialog, Message
from sim.adapters.db.session import transaction
from sim.ports.llm import LLMError
from sim.services import jobkinds
from sim.services.app_settings import AppSettingsService
from sim.services.incoming_media import IncomingMedia
from sim.services.media_recognition import DEFAULT_MODEL, MAX_FILE_BYTES, SubscriptionMediaRecognizer
from sim.worker.handlers import HANDLERS
from sim.worker.loop import Worker
from tests.conftest import inbound
from tests.test_broker_reconciliation import DONE, adapter
from tests.test_ingest import ingest
from tests.test_media import drain, jobs_of

CATALOG = [{"id": DEFAULT_MODEL, "billing_type": "subscription", "available": True,
    "capabilities": ["chat:fast"], "job_request": {"message_content": ["text", "image_data_url"]}}]


async def enable(container, connector, fake_llm, monkeypatch):
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("incoming_media_subscription", 1, None)
    monkeypatch.setattr(connector.port, "download_media_bounded", AsyncMock(return_value=b"synthetic document"), raising=False)
    monkeypatch.setattr(fake_llm, "list_models", AsyncMock(return_value=CATALOG))
    fake_llm.replies = ["Синтетический документ"]


async def run_media(container):
    await drain(container, Worker(container, {jobkinds.MEDIA: HANDLERS[jobkinds.MEDIA]}))


async def test_opt_in_queue_preserves_caption_status_context_and_pause(container, world, connector, fake_llm, monkeypatch):
    await enable(container, connector, fake_llm, monkeypatch)
    res = await ingest(container, world.channel.id, inbound("Моя подпись", ext="doc", media_kind="document", media_url="tg-file:doc"))
    assert res.message.media_status == "pending"
    assert not connector.port.download_media_bounded.called and not fake_llm.calls
    async with transaction(container.factory) as s:
        d = await s.get(Dialog, res.dialog.id)
        d.ai_paused, d.needs_operator = True, True
    await run_media(container)
    async with container.factory() as s:
        m, d = await s.get(Message, res.message.id), await s.get(Dialog, res.dialog.id)
        assert m.media_status == "done" and m.media_reason is None
        assert "Моя подпись" in m.text and "Синтетический документ" in m.text
        assert m.media_url.startswith("/api/media/")
        assert d.ai_paused and d.needs_operator and "Синтетический документ" in d.last_message_text
    assert len(fake_llm.calls) == 1 and connector.port.sent == []
    connector.port.download_media_bounded.assert_awaited_once_with("tg-file:doc", max_bytes=MAX_FILE_BYTES)


@pytest.mark.parametrize("kind,data", [("voice", b"audio"), ("audio", b"audio"), ("document", b"%PDF-1.7"), ("document", b"PK\x03\x04" + b"x" * 100)])
async def test_unsupported_never_uses_paid_transcription(container, world, connector, fake_llm, monkeypatch, kind, data):
    await enable(container, connector, fake_llm, monkeypatch)
    connector.port.download_media_bounded.return_value = data
    transcribe = AsyncMock(side_effect=AssertionError("paid path forbidden"))
    monkeypatch.setattr(fake_llm, "transcribe", transcribe)
    res = await ingest(container, world.channel.id, inbound("caption", ext="u", media_kind=kind, media_url="tg-file:u"))
    await run_media(container)
    async with container.factory() as s:
        m = await s.get(Message, res.message.id)
        assert m.media_status == "unsupported" and "caption" in m.text
        d = await s.get(Dialog, res.dialog.id)
        assert not d.ai_paused and not d.needs_operator
    assert not fake_llm.calls and not transcribe.called


async def test_disabled_does_not_queue_new_document(container, world):
    res = await ingest(container, world.channel.id, inbound("caption", ext="off", media_kind="document", media_url="tg-file:d"))
    assert res.message.media_status is None
    assert await jobs_of(container, jobkinds.MEDIA) == []


async def test_failure_is_explicit_and_not_handoff(container, world, connector, fake_llm, monkeypatch):
    await enable(container, connector, fake_llm, monkeypatch)
    connector.port.download_media_bounded.side_effect = httpx.ReadTimeout("synthetic")
    res = await ingest(container, world.channel.id, inbound("caption", ext="fail", media_kind="image", media_url="tg-file:f"))
    await run_media(container)
    async with container.factory() as s:
        m = await s.get(Message, res.message.id)
        assert m.media_status == "failed" and m.media_reason == "recognition_failed"
        assert "caption" in m.text and not (await s.get(Dialog, res.dialog.id)).ai_paused
    assert not fake_llm.calls


async def test_late_finish_does_not_duplicate_or_replace_new_preview(container, world, connector, fake_llm, monkeypatch):
    await enable(container, connector, fake_llm, monkeypatch)
    res = await ingest(container, world.channel.id, inbound("caption", ext="first", media_kind="document", media_url="tg-file:d"))
    await ingest(container, world.channel.id, inbound("Новая реплика", ext="second"))
    await run_media(container)
    async with transaction(container.factory) as s:
        svc = IncomingMedia(container.media_service(s))
        await svc.finish(res.message.id, "done", None, result="duplicate")
        m = await s.get(Message, res.message.id)
        assert "duplicate" not in m.text
        assert (await s.get(Dialog, res.dialog.id)).last_message_text == "Новая реплика"


def test_download_buffer_rejects_before_growth():
    with BoundedMediaBuffer(5) as b:
        b.write(b"1234")
        with pytest.raises(ValueError):
            b.write(b"56")
        assert b.getvalue() == b"1234"


async def test_subscription_recognition_recovers_unknown_post_without_new_job(container, monkeypatch):
    posts = []

    def transport(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": CATALOG})
        if request.method == "POST":
            posts.append(request.headers["Idempotency-Key"])
            raise httpx.ReadTimeout("unknown outcome", request=request)
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"pagination_complete": True, "next_cursor": None,
                "jobs": [{"job_id": 72, "idempotency_key": posts[0], "labels": {"sim_request": posts[0]}}]})
        return httpx.Response(200, json=DONE)

    for generation in (1, 2):
        broker, _, _ = adapter(container, monkeypatch, generation, transport)
        broker.subscription_budget_s = -1
        recognizer = SubscriptionMediaRecognizer(broker)
        if generation == 1:
            with pytest.raises(LLMError):
                await recognizer.recognize(b"same synthetic document", "document")
        else:
            assert await recognizer.recognize(b"same synthetic document", "document") == DONE["text"]
        await broker.aclose()
    assert len(posts) == 1
