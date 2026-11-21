"""BrokerSpeech: synchronous TTS → WAV."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.speech import MAX_TEXT_CHARS, BrokerSpeech, speech_body
from sim.ports import PortError
from tests.adapters.helpers import sent_json, wav_bytes

WAV = wav_bytes(0.1)


def spoken(**fields: object) -> httpx.Response:
    return httpx.Response(200, json={
        "audio_base64": base64.b64encode(WAV).decode(), "mime_type": "audio/wav",
        "audio_s": 0.1, "model": "gemini/gemini-3.8-flash-tts", "cost_usd": 0.0002,
        "tokens_in": 6, "tokens_out": 3, **fields})


async def test_synthesize(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/speech").mock(return_value=spoken())
    result = await BrokerSpeech(http).synthesize(
        text="  приветик  ", voice="Vindemiatrix", style="тихо, " + "x" * 400,
        model="gemini/gemini-3.8-flash-tts")
    body = sent_json(post)
    assert body["text"] == "приветик"
    assert body["voice"] == "Vindemiatrix"
    assert len(body["style"]) == 300
    assert body["model"] == "gemini/gemini-3.8-flash-tts"
    assert body["workflow"] == "sim-voice-note"
    assert result.data == WAV
    assert result.mime == "audio/wav"
    assert result.duration_s == pytest.approx(0.1)
    assert result.usage.cost_usd == pytest.approx(0.0002)
    assert result.usage.job_id is None


def test_body_leaves_out_empty_options() -> None:
    body = speech_body(text="hi", voice="", style="  ", model="", workflow="w")
    assert body == {"text": "hi", "workflow": "w"}


@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_TEXT_CHARS + 1)])
def test_body_rejects_bad_text(text: str) -> None:
    with pytest.raises(PortError) as info:
        speech_body(text=text, voice="Kore", style="", model="", workflow="w")
    assert info.value.retryable is False


async def test_answer_without_audio(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/speech").mock(return_value=spoken(audio_base64=""))
    with pytest.raises(PortError, match="no media") as info:
        await BrokerSpeech(http).synthesize(text="hi", voice="Kore", style="", model="")
    assert info.value.retryable is False


async def test_defaults_when_fields_missing(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/speech").mock(return_value=httpx.Response(
        200, json={"audio_base64": base64.b64encode(WAV).decode()}))
    result = await BrokerSpeech(http).synthesize(text="hi", voice="Kore", style="",
                                                 model="pinned")
    assert result.mime == "audio/wav"
    assert result.duration_s is None
    assert result.usage.model == "pinned"


@pytest.mark.parametrize(("response", "retryable"), [
    (httpx.Response(502, text="daily budget cap reached — retry after 00:00 UTC"), False),
    (httpx.Response(502, text="every key failed"), True),
    (httpx.Response(422, text="unknown voice"), False),
])
async def test_speech_errors(http: BrokerHttp, broker: respx.MockRouter,
                             response: httpx.Response, retryable: bool) -> None:
    broker.post("/v1/speech").mock(return_value=response)
    with pytest.raises(PortError) as info:
        await BrokerSpeech(http).synthesize(text="hi", voice="Kore", style="", model="")
    assert info.value.retryable is retryable
