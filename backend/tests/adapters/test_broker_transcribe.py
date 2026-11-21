"""BrokerTranscriber: keyed jobs, idempotency, recovery of a lost submit, no fallbacks."""
from __future__ import annotations

import httpx
import pytest
import respx

from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.transcribe import BrokerTranscriber, idempotency_key
from sim.ports import PortError
from tests.adapters.helpers import PROJECT_KEY, done, failed, submitted

AUDIO = b"OggS fake voice note"
KEY = idempotency_key(AUDIO, "voice.ogg", "sim-voice")
KEYED = "/v1/transcribe/keyed-jobs"
JOB = "/v1/transcribe/jobs/9"
BY_KEY = f"/v1/transcribe/jobs/by-key/{KEY}"


def accepted() -> httpx.Response:
    return submitted(9, poll_url=JOB)


def transcript() -> httpx.Response:
    return done(9, text="  привет, как дела?  ", model="gemini/gemini-3.5-transcribe",
                cost_usd=0.0008, provider="gemini")


async def test_transcribe(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post(KEYED).mock(return_value=accepted())
    broker.get(JOB).mock(return_value=transcript())
    result = await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg",
                                                      model="ignored")
    assert result.text == "привет, как дела?"
    assert result.usage.model == "gemini/gemini-3.5-transcribe"
    assert result.usage.cost_usd == pytest.approx(0.0008)
    assert result.usage.job_id == "9"
    request = post.calls.last.request
    assert request.headers["Idempotency-Key"] == KEY
    assert request.headers["X-Project-Key"] == PROJECT_KEY
    assert request.url.params["workflow"] == "sim-voice"
    body = request.read()
    assert b'name="file"; filename="voice.ogg"' in body
    assert AUDIO in body
    assert b"ignored" not in body


async def test_replay_of_finished_job(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post(KEYED).mock(return_value=transcript())
    poll = broker.get(JOB)
    result = await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg")
    assert result.text == "привет, как дела?"
    assert poll.call_count == 0


async def test_lost_submit_is_found_by_key(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post(KEYED).mock(side_effect=httpx.ReadTimeout)
    lookup = broker.get(BY_KEY).mock(return_value=httpx.Response(
        200, json={"job_id": 9, "status": "running", "poll_url": JOB}))
    broker.get(JOB).mock(return_value=transcript())
    result = await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg")
    assert result.text == "привет, как дела?"
    assert (post.call_count, lookup.call_count) == (1, 1)


async def test_lost_submit_not_found_is_retryable(http: BrokerHttp,
                                                  broker: respx.MockRouter) -> None:
    post = broker.post(KEYED).mock(return_value=httpx.Response(503, text="busy"))
    broker.get(BY_KEY).mock(return_value=httpx.Response(404, text="no such key"))
    with pytest.raises(PortError) as info:
        await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg")
    assert info.value.retryable is True
    assert post.call_count == 1               # never resubmitted, never another route


async def test_missing_keyed_route_is_retryable(http: BrokerHttp,
                                                broker: respx.MockRouter) -> None:
    broker.post(KEYED).mock(return_value=httpx.Response(404, text="Not Found"))
    old_async = broker.post("/v1/transcribe/jobs")
    old_sync = broker.post("/v1/transcribe")
    with pytest.raises(PortError, match="keyed transcription route missing") as info:
        await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg")
    assert info.value.retryable is True
    assert old_async.call_count == 0
    assert old_sync.call_count == 0


async def test_rejected_upload_is_final(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post(KEYED).mock(return_value=httpx.Response(413, text="too large"))
    lookup = broker.get(BY_KEY)
    with pytest.raises(PortError) as info:
        await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg")
    assert info.value.retryable is False
    assert lookup.call_count == 0


async def test_failed_transcription(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post(KEYED).mock(return_value=accepted())
    broker.get(JOB).mock(return_value=failed(9, error="transcript too large",
                                             error_code="transcript_too_large"))
    with pytest.raises(PortError, match="transcript_too_large") as info:
        await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="voice.ogg")
    assert info.value.retryable is False


async def test_empty_audio(http: BrokerHttp) -> None:
    with pytest.raises(PortError, match="empty audio"):
        await BrokerTranscriber(http).transcribe(audio=b"", filename="voice.ogg")


async def test_default_filename(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post(KEYED).mock(return_value=transcript())
    await BrokerTranscriber(http).transcribe(audio=AUDIO, filename="")
    assert post.calls.last.request.headers["Idempotency-Key"] == KEY


def test_idempotency_key_is_stable_and_valid() -> None:
    key = idempotency_key(b"abc", "a.ogg", "sim-voice")
    assert key == idempotency_key(b"abc", "a.ogg", "sim-voice")
    assert key != idempotency_key(b"abd", "a.ogg", "sim-voice")
    assert key != idempotency_key(b"abc", "b.ogg", "sim-voice")
    assert key != idempotency_key(b"abc", "a.ogg", "other")
    assert key.startswith("sim-tr-")
    assert len(key) <= 128
    assert all(ch.isalnum() or ch in "._:-" for ch in key)
