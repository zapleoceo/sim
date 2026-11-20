"""Attachment staging uses private result access and never resubmits lost ASR requests."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import PreviewAttachmentOperation, TraceEvent
from sim.ports.llm import LLMError
from sim.ports.preview_asr import AsrReceipt
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, decision_json, login
from tests.test_preview_operations import start
from tests.test_preview_speech_jobs import LostAckSpeech


class LostAckAsr:
    def __init__(self):
        self.posts = []
        self.lookups = []

    async def submit(self, key, data, filename):
        self.posts.append((key, data, filename))
        raise LLMError("synthetic lost receipt")

    async def by_key(self, key):
        self.lookups.append(key)
        return AsrReceipt("42", "done", "Распознанная речь")


async def setup_attachment(api, container, world):
    port = LostAckAsr()
    container.preview_asr = port
    container.preview_speech = LostAckSpeech()
    container.settings.preview_speech_jobs_enabled = True
    container.settings.preview_attachment_queue_enabled = True
    auth, path = await start(api, container, world)
    return port, auth, path.replace("preview-operations", "preview/attachment-operations")


async def test_attachment_local_text_and_private_file(api, container, world):
    port, auth, path = await setup_attachment(api, container, world)
    body = {"file": ("private.txt", "Привет".encode(), "text/plain")}
    assert (await api.post(path, files=body)).status_code == 403
    assert (await api.post(path, files=body, headers=auth)).status_code == 202
    response = await api.get(path)
    assert response.json()["result"]["text"] == "[файл private.txt]\nПривет"
    assert response.headers["cache-control"] == "no-store"
    assert not port.posts and await Worker(container).tick() == 0
    assert (await api.get(path + "/file")).content == "Привет".encode()
    await login(api, *OPERATOR)
    assert (await api.get(path)).status_code == 404
    assert (await api.get(path + "/file")).status_code == 404


async def test_attachment_lost_asr_receipt_reuses_key_without_reposting(api, container, world):
    port, auth, path = await setup_attachment(api, container, world)
    body = {"file": ("note.ogg", b"OggS test", "audio/ogg")}
    assert (await api.post(path, files=body, headers=auth)).status_code == 202
    assert (await api.post(path, files=body, headers=auth)).status_code == 202
    assert (await api.get(path)).json()["status"] == "running"
    worker = Worker(container)
    assert await worker.tick() == 1
    now = container.clock()
    container.clock = lambda: now + timedelta(seconds=4)
    assert await worker.tick() == 1
    assert (await api.get(path)).json()["result"]["text"] == "[аудио note.ogg: Распознанная речь]"
    assert len(port.posts) == 1 and port.lookups == [port.posts[0][0]]


@pytest.mark.parametrize(("filename", "data", "mime"), [
    ("empty.txt", b"", "text/plain"),
    ("unsupported.zip", b"not an accepted attachment", "application/zip"),
    ("large.txt", b"x" * (10 * 1024 * 1024 + 1), "text/plain"),
])
async def test_attachment_returns_422_for_invalid_upload(api, container, world, filename, data, mime):
    port, auth, path = await setup_attachment(api, container, world)
    assert (await api.post(path, files={"file": (filename, data, mime)}, headers=auth)).status_code == 422
    assert not port.posts and await Worker(container).tick() == 0


async def test_attachment_limits_and_retention(api, container, world):
    _, auth, path = await setup_attachment(api, container, world)
    assert (await api.post(path, files={"file": ("sample.txt", b"content", "text/plain")},
                           headers=auth)).status_code == 202
    now = container.clock()
    container.clock = lambda: now + timedelta(hours=2)
    assert (await api.get(path)).json() == {"status": "expired"}
    assert (await api.get(path + "/file")).status_code == 404
    await Worker(container).cleanup_previews()
    async with container.factory() as s:
        row = await s.get(PreviewAttachmentOperation, path.rsplit("/", 1)[1])
        assert row is not None and row.input_cipher is None and row.result_cipher is None


async def test_attachment_vision_cap_rejects_before_enqueue(api, container, world):
    port, auth, path = await setup_attachment(api, container, world)
    response = await api.post(path, files={"file": ("large.png", b"x" * (1400 * 1024 + 1), "image/png")},
                              headers=auth)
    assert response.status_code == 422 and "1,4 МиБ" in response.text
    assert not port.posts and await Worker(container).tick() == 0


async def test_attachment_send_uses_private_preview_trace(api, container, world, fake_llm):
    _, auth, path = await setup_attachment(api, container, world)
    marker = "private-attachment-content"
    assert (await api.post(path, files={"file": ("note.txt", marker.encode(), "text/plain")},
                           headers=auth)).status_code == 202
    attachment = (await api.get(path)).json()["result"]
    preview_path = path.replace("preview/attachment-operations", "preview-operations")
    fake_llm.replies = [decision_json(reply="Принято", stage="engaged")]
    assert (await api.post(preview_path, json={"message": attachment["text"]}, headers=auth)).status_code == 202
    assert await Worker(container).tick() == 1
    async with container.factory() as s:
        events = (await s.scalars(select(TraceEvent))).all()
        assert any("omitted" in str(event.detail) for event in events)
        assert all(marker not in str(event.detail) for event in events)
        assert all("broker_exchange" not in (event.detail or {}) for event in events)
