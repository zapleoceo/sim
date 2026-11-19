"""Offline streaming transport, no provider probes."""
import asyncio
import base64
import json

import httpx
import pytest

from sim.adapters.llm.broker import BrokerLLM
from sim.adapters.studio_broker import StudioBroker
from sim.ports.video import VideoError
from sim.services.studio_files import valid_artifact
from tests.studio_fixtures import mp4_bytes, wav_bytes


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, gate=None):
        self.chunks, self.gate = chunks, gate
        self.closed, self.read = False, 0

    async def __aiter__(self):
        if self.gate is not None:
            self.gate.set()
            await asyncio.Event().wait()
        for chunk in self.chunks:
            self.read += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def adapter(stream, headers=None, status=200):
    broker = BrokerLLM("https://broker.test", "synthetic-key")
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})}, stream=stream)
    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return broker, StudioBroker(broker), calls


@pytest.mark.parametrize("headers", [{}, {"content-length": "999999"}, {"content-encoding": "gzip"}])
async def test_stream_limits_close_without_retry(headers):
    stream = Stream([b"x" * 65536] * 8)
    broker, media, calls = adapter(stream, headers)
    try:
        with pytest.raises(VideoError):
            await media.request("POST", "/v1/speech", 1024)
        assert stream.closed and len(calls) == 1
        assert stream.read <= 65536
    finally:
        await broker.aclose()


@pytest.mark.parametrize("cancel", [True, False])
async def test_cancel_timeout_close_without_resubmit(cancel):
    gate = asyncio.Event()
    stream = Stream([], gate)
    broker, media, calls = adapter(stream)
    try:
        task = asyncio.create_task(media.request("POST", "/v1/speech", 1024, seconds=1 if cancel else .01))
        await gate.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else VideoError):
            await task
        assert stream.closed and len(calls) == 1
    finally:
        await broker.aclose()


async def test_known_video_read_constructs_local_path_and_decodes_bound():
    stream = Stream([json.dumps({"status": "done", "video_base64": base64.b64encode(mp4_bytes()).decode(),
                                "mime_type": "video/mp4"}).encode()])
    broker, media, calls = adapter(stream)
    try:
        result = await media.read_video("42")
        assert result.video == mp4_bytes()
        assert str(calls[0].url) == "https://broker.test/v1/jobs/42"
        assert calls[0].method == "GET"
        assert calls[0].headers["accept-encoding"] == "identity"
        with pytest.raises(VideoError):
            await media.read_video("../../other")
        assert len(calls) == 1
    finally:
        await broker.aclose()


async def test_submit_ignores_untrusted_poll_url_and_does_not_poll_automatically():
    stream = Stream([b'{"job_id":42,"poll_url":"https://evil.test/secret"}'])
    broker, media, calls = adapter(stream, status=202)
    try:
        assert await media.submit_video("Synthetic") == "42"
        assert len(calls) == 1 and calls[0].url.path == "/v1/video/jobs"
    finally:
        await broker.aclose()


async def test_studio_speech_transport_pins_single_broker_submission():
    stream = Stream([json.dumps({"audio_base64": base64.b64encode(wav_bytes()).decode(),
                                "mime_type": "audio/wav"}).encode()])
    broker, media, calls = adapter(stream)
    try:
        result = await media.synthesize("Synthetic", voice="Kore", workflow="studio")
        assert result.audio == wav_bytes()
        assert [(call.method, call.url.path) for call in calls] == [("POST", "/v1/speech")]
        assert json.loads(calls[0].content)["model"] == "gemini/gemini-3.8-flash-tts"
    finally:
        await broker.aclose()


async def test_redirect_is_refused_without_following_or_reading():
    stream = Stream([b"private"])
    broker, media, calls = adapter(stream, {"location": "https://evil.test"}, status=302)
    try:
        with pytest.raises(VideoError):
            await media.request("POST", "/v1/speech", 1024)
        assert len(calls) == 1 and stream.read == 0 and stream.closed
    finally:
        await broker.aclose()


def test_signatures_and_truncated_containers_fail_closed():
    assert valid_artifact(wav_bytes(), "audio/wav", "speech")
    assert valid_artifact(mp4_bytes(), "video/mp4", "video")
    for data, mime, kind in [(b"<script>x</script>", "video/mp4", "video"),
                             (wav_bytes()[:-1], "audio/wav", "speech"),
                             (mp4_bytes()[:-1], "video/mp4", "video"),
                             (mp4_bytes(), "audio/wav", "speech"),
                             (b"OggSfake", "audio/ogg", "speech")]:
        assert not valid_artifact(data, mime, kind)


def test_decoded_size_limit_checked_before_allocation():
    with pytest.raises(VideoError):
        StudioBroker.decode({"video_base64": "A" * 100}, "video_base64", 8)
