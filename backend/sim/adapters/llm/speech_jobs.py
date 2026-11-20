"""Project-scoped Broker speech requests. Never follow paths returned by a receipt."""
from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from sim.ports.speech import SpeechError
from sim.ports.speech_jobs import InvalidSpeechAudio, SpeechJob, SpeechResultExpired

MAX_WAV_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class BrokerSpeechJobs:
    origin: str
    project_key: str
    client: Callable[[], httpx.AsyncClient]

    async def submit_speech(self, key: str, body: dict[str, str]) -> SpeechJob:
        receipt = await self.receipt("POST", "/v1/speech/jobs", key=key, body=body)
        if receipt is None:
            raise SpeechError("Speech submission receipt unavailable")
        return receipt

    async def speech_by_key(self, key: str) -> SpeechJob | None:
        return await self.receipt("GET", f"/v1/speech/jobs/by-key/{key}", missing_ok=True)

    async def speech_status(self, job_id: int) -> SpeechJob:
        receipt = await self.receipt("GET", f"/v1/speech/jobs/{job_id}")
        if receipt is None or receipt.job_id != job_id:
            raise SpeechError("Speech receipt mismatch")
        return receipt

    async def receipt(self, method: str, path: str, *, key: str | None = None,
                      body: dict[str, str] | None = None, missing_ok: bool = False) -> SpeechJob | None:
        headers = {"X-Project-Key": self.project_key}
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            async with asyncio.timeout(8):
                async with self.client().stream(
                    method, self.origin + path, headers=headers, json=body, follow_redirects=False,
                ) as response:
                    if missing_ok and response.status_code == 404:
                        return None
                    if response.status_code not in ((200, 202) if method == "POST" else (200,)):
                        raise SpeechError("Speech receipt unavailable")
                    data = await bounded(response, 32 * 1024)
            return SpeechJob.model_validate_json(data)
        except (httpx.HTTPError, TimeoutError, ValidationError) as exc:
            raise SpeechError("Speech receipt unavailable") from exc

    async def speech_result(self, job_id: int) -> bytes:
        try:
            async with asyncio.timeout(8):
                async with self.client().stream(
                    "GET", f"{self.origin}/v1/speech/jobs/{job_id}/result",
                    headers={"X-Project-Key": self.project_key}, follow_redirects=False,
                ) as response:
                    if response.status_code == 410:
                        raise SpeechResultExpired("Speech result expired")
                    if response.status_code != 200:
                        raise SpeechError("Speech result unavailable")
                    if (length := response.headers.get("Content-Length")) is not None:
                        if not length.isascii() or not length.isdigit() or not 0 < int(length) <= MAX_WAV_BYTES:
                            raise InvalidSpeechAudio("Invalid speech result length")
                    data = await bounded(response, MAX_WAV_BYTES, InvalidSpeechAudio)
                    return normalize_audio(
                        data, response.headers.get("Content-Type", ""),
                        response.headers.get("X-PCM-Format", ""),
                    )
        except (httpx.HTTPError, TimeoutError) as exc:
            raise SpeechError("Speech result unavailable") from exc


def normalize_audio(data: bytes, content_type: str, pcm_format: str) -> bytes:
    media_type, *params = (part.strip().lower() for part in content_type.split(";"))
    if media_type == "audio/wav" and not params:
        try:
            with wave.open(io.BytesIO(data), "rb") as source:
                frames = source.getnframes()
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24000):
                    raise InvalidSpeechAudio("Unexpected WAV format")
                if frames <= 0 or len(source.readframes(frames)) != frames * 2:
                    raise InvalidSpeechAudio("Invalid WAV frame count")
        except (wave.Error, EOFError) as exc:
            raise InvalidSpeechAudio("Invalid WAV result") from exc
        return data
    if media_type != "audio/l16" or sorted(params) != ["codec=pcm", "rate=24000"]:
        raise InvalidSpeechAudio("Unsupported speech result type")
    if pcm_format.lower() != "s16le":
        raise InvalidSpeechAudio("Broker did not confirm raw PCM byte order")
    if not data or len(data) % 2 or data.startswith((b"RIFF", b"RIFX")):
        raise InvalidSpeechAudio("Invalid raw PCM frame count")
    out = io.BytesIO()
    with wave.open(out, "wb") as destination:
        destination.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        destination.writeframes(data)
    return out.getvalue()


async def bounded(response: httpx.Response, maximum: int, error: type[SpeechError] = SpeechError) -> bytes:
    data = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(data) + len(chunk) > maximum:
            raise error("Speech response exceeds limit")
        data.extend(chunk)
    return bytes(data)
