"""SpeechPort over the synchronous `POST /v1/speech` (Gemini TTS): text → WAV, base64 in the
answer. SIM turns the WAV into an OGG/Opus voice note itself (`media_tools.wav_to_ogg_opus`)."""
from __future__ import annotations

from typing import Any

import httpx

from sim.adapters.broker.http import BrokerHttp, as_float, decode_b64, usage_of
from sim.ports import AudioResult, PortError

SPEECH_PATH = "/v1/speech"
MAX_TEXT_CHARS = 5000
MAX_STYLE_CHARS = 300
DEFAULT_WORKFLOW = "sim-voice-note"
# The Broker's own Gemini TTS ceiling is 60 s per key attempt; leave room for a second key.
SPEECH_TIMEOUT = httpx.Timeout(connect=5.0, read=150.0, write=10.0, pool=5.0)


def speech_body(*, text: str, voice: str, style: str, model: str,
                workflow: str) -> dict[str, Any]:
    clean = text.strip()
    if not clean:
        raise PortError("speech: empty text")
    if len(clean) > MAX_TEXT_CHARS:
        raise PortError(f"speech: text longer than {MAX_TEXT_CHARS} characters")
    body: dict[str, Any] = {"text": clean, "workflow": workflow}
    if voice:
        body["voice"] = voice
    if style.strip():
        body["style"] = style.strip()[:MAX_STYLE_CHARS]
    if model:
        body["model"] = model
    return body


class BrokerSpeech:
    def __init__(self, http: BrokerHttp, *, workflow: str = DEFAULT_WORKFLOW) -> None:
        self.http = http
        self.workflow = workflow

    async def synthesize(self, *, text: str, voice: str, style: str,
                         model: str) -> AudioResult:
        body = speech_body(text=text, voice=voice, style=style, model=model,
                           workflow=self.workflow)
        start = self.http.clock()
        async with self.http.slot("speech"):
            data = await self.http.json("POST", SPEECH_PATH, what="speech", json=body,
                                        http_timeout=SPEECH_TIMEOUT)
        audio = decode_b64(data.get("audio_base64"), what="speech")
        return AudioResult(
            data=audio,
            mime=str(data.get("mime_type") or "audio/wav"),
            duration_s=as_float(data.get("audio_s")),
            usage=usage_of(data, model=model, latency_ms=self.http.elapsed_ms(start),
                           job_id=None),
        )
