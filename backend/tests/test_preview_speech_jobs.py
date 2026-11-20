"""Preview speech is a separately recoverable operation following text completion."""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.adapters.llm.speech_jobs import BrokerSpeechJobs, normalize_audio
from sim.domain.enums import VoiceMode
from sim.ports.speech import SpeechError
from sim.ports.speech_jobs import InvalidSpeechAudio, SpeechJob
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, decision_json, login
from tests.test_preview_operations import start
from tests.test_voice import FakeEncoder


RAW_PCM = b"\x34\x12\xfe\xff"
PCM_MIME = "audio/L16;codec=pcm;rate=24000"
WAV_BYTES = normalize_audio(RAW_PCM, PCM_MIME, "s16le")


class LostAckSpeech:
    def __init__(self):
        self.posts = []
        self.lookups = []

    async def submit_speech(self, key, body):
        self.posts.append((key, body))
        raise SpeechError("lost receipt")

    async def speech_by_key(self, key):
        self.lookups.append(key)
        return SpeechJob(job_id=41, status="done")

    async def speech_result(self, job_id):
        assert job_id == 41
        return WAV_BYTES


async def test_preview_returns_text_first_and_recovers_audio_without_second_post(api, container, world, fake_llm):
    speech = LostAckSpeech()
    container.preview_speech = speech
    container.encoder = FakeEncoder()
    container.settings.preview_speech_jobs_enabled = True
    auth, path = await start(api, container, world)
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.voice_id, persona.voice_mode = "Kore", VoiceMode.ALWAYS
    assert (await api.post(path, json={"message": "Привет"}, headers=auth)).status_code == 202
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.voice_mode = VoiceMode.OFF
    fake_llm.replies = [decision_json(reply="Проверка", stage="engaged")]
    worker = Worker(container)
    assert await worker.tick() == 1
    pending = (await api.get(path)).json()
    assert pending["status"] == "complete" and pending["result"]["reply"]
    assert pending["result"]["voice"]["status"] == "pending"
    assert not speech.posts

    assert await worker.tick() == 1
    now = container.clock()
    container.clock = lambda: now + timedelta(seconds=4)
    assert await worker.tick() == 1
    assert len(speech.posts) == 1 and speech.lookups == [speech.posts[0][0]]
    assert speech.posts[0][0].startswith("tts-")
    ready = (await api.get(path)).json()
    assert ready["result"]["reply"] == pending["result"]["reply"]
    audio = await api.get(ready["result"]["voice"]["media_url"])
    assert audio.content.startswith(b"OggS") and audio.headers["cache-control"] == "no-store"
    await login(api, *OPERATOR)
    assert (await api.get(ready["result"]["voice"]["media_url"])).status_code == 404


async def test_speech_broker_does_not_follow_receipt_url():
    calls = []

    def route(request):
        calls.append((request.method, str(request.url), request.headers.get("Idempotency-Key")))
        if request.url.path.endswith("/result"):
            return httpx.Response(200, headers={"Content-Type": "audio/wav"}, content=WAV_BYTES)
        return httpx.Response(202 if request.method == "POST" else 200, json={
            "job_id": 7, "status": "done", "status_url": "https://other.invalid/",
            "result_url": "https://other.invalid/",
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        port = BrokerSpeechJobs("https://broker.invalid", "synthetic", lambda: client)
        key = "tts-1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3"
        receipt = await port.submit_speech(key, {"text": "test", "voice": "Kore"})
        assert (await port.speech_by_key(key)) == receipt
        assert (await port.speech_status(receipt.job_id)) == receipt
        assert await port.speech_result(receipt.job_id) == WAV_BYTES
    assert len(calls) == 4 and all(url.startswith("https://broker.invalid/") for _, url, _ in calls)
    assert calls[0][2] == key


@pytest.mark.parametrize(("mime", "pcm_format", "body"), [
    ("audio/wav", "", WAV_BYTES),
    (PCM_MIME, "s16le", RAW_PCM),
    (PCM_MIME, "", RAW_PCM),
    (PCM_MIME, "s16le", b"x"),
    ("audio/mpeg", "", WAV_BYTES),
])
async def test_speech_result_validates_and_normalizes_mime(mime, pcm_format, body):
    def route(_request):
        return httpx.Response(200, headers={"Content-Type": mime, "X-PCM-Format": pcm_format}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        port = BrokerSpeechJobs("https://broker.invalid", "synthetic", lambda: client)
        if body == WAV_BYTES and mime == "audio/wav" or body == RAW_PCM and pcm_format == "s16le":
            assert await port.speech_result(7) == WAV_BYTES
            assert WAV_BYTES.endswith(RAW_PCM)
        else:
            with pytest.raises(InvalidSpeechAudio):
                await port.speech_result(7)


async def test_preview_advertises_legacy_queue_and_new_readiness_separately(api, container, world):
    container.preview_speech = LostAckSpeech()
    await start(api, container, world)
    url = f"/api/personas/{world.persona.id}"
    old = (await api.get(url)).json()
    assert old["preview_async_enabled"] is True and old["preview_speech_jobs_ready"] is False
    container.settings.preview_speech_jobs_enabled = True
    new = (await api.get(url)).json()
    assert new["preview_async_enabled"] is True and new["preview_speech_jobs_ready"] is True
