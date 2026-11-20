"""Joint offline coverage for Studio compatibility and private ASR to reply to speech."""
from __future__ import annotations

from datetime import timedelta

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import VoiceMode
from sim.ports.preview_asr import AsrReceipt
from sim.worker.loop import Worker
from tests.conftest import decision_json
from tests.test_preview_attachment_queue import setup_attachment
from tests.test_voice import FakeEncoder


class DoneAsr:
    async def submit(self, _key, _data, _filename):
        return AsrReceipt("17", "done", "Синтетический транскрипт")


async def test_combined_flags_preserve_144_and_close_legacy_only_after_activation(api, container, world, fake_llm):
    _, auth, _ = await setup_attachment(api, container, world)
    container.settings.preview_speech_jobs_enabled = False
    base = f"/api/personas/{world.persona.id}"
    legacy = (await api.get(base)).json()
    assert legacy["preview_async_enabled"] is True
    assert legacy["preview_speech_jobs_ready"] is False
    assert legacy["preview_attachments_async"] is False
    fake_llm.replies = [decision_json(reply="Старый маршрут", stage="engaged")]
    assert (await api.post(base + "/preview", json={"message": "Привет"}, headers=auth)).status_code == 200

    container.settings.preview_speech_jobs_enabled = True
    active = (await api.get(base)).json()
    assert active["preview_speech_jobs_ready"] is True and active["preview_attachments_async"] is True
    calls = len(fake_llm.calls)
    assert (await api.post(base + "/preview", json={"message": "Привет"}, headers=auth)).status_code == 409
    assert (await api.post(base + "/preview/attachment", files={"file": ("x.txt", b"test", "text/plain")},
                           headers=auth)).status_code == 409
    assert len(fake_llm.calls) == calls


async def test_combined_voice_attachment_text_is_available_before_audio(api, container, world, fake_llm):
    _, auth, attachment = await setup_attachment(api, container, world)
    container.preview_asr = DoneAsr()
    container.encoder = FakeEncoder()
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.voice_id, persona.voice_mode = "Kore", VoiceMode.MIRROR
    assert (await api.post(attachment, files={"file": ("input.ogg", b"OggS test", "audio/ogg")},
                           data={"voice": "true"}, headers=auth)).status_code == 202
    worker = Worker(container)
    assert await worker.run_preview_attachment() == 1
    staged = (await api.get(attachment)).json()["result"]
    assert staged["kind"] == "voice" and staged["text"] == "Синтетический транскрипт"

    preview = attachment.replace("preview/attachment-operations", "preview-operations")
    fake_llm.replies = [decision_json(reply="Текст до аудио", stage="engaged")]
    assert (await api.post(preview, json={"message": staged["text"], "voice_input": True},
                           headers=auth)).status_code == 202
    for claim in await worker.claim_preview():
        await worker.run_job(*claim)
    before = (await api.get(preview)).json()["result"]
    assert before["reply"] and before["voice"]["status"] == "pending"
    assert await worker.run_preview_speech_step() == 1
    now = container.clock()
    container.clock = lambda: now + timedelta(seconds=4)
    assert await worker.run_preview_speech_step() == 1
    result = (await api.get(preview)).json()["result"]
    assert result["reply"] == before["reply"]
    assert result["voice"]["status"] == "done"
