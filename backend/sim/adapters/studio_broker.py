"""Bounded Studio-only Broker media transport; never retries a submission."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json

import httpx

from sim.adapters.llm.broker import BrokerLLM
from sim.ports.speech import SpeechError, SpeechResult
from sim.ports.video import VideoError, VideoResult


class StudioBroker:
    def __init__(self, broker: BrokerLLM):
        self.broker = broker
        self.scope = broker.request_scope

    async def request(self, method: str, path: str, limit: int, *, body=None, seconds=35):
        try:
            async with asyncio.timeout(seconds):
                async with self.broker.client.stream(
                    method, self.broker.url + path,
                    headers={**self.broker.headers, "Accept-Encoding": "identity"},
                    json=body, timeout=httpx.Timeout(seconds, connect=5), follow_redirects=False,
                ) as response:
                    if response.status_code != 200 and response.status_code != 202:
                        raise ValueError("Broker HTTP refusal")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Compressed response refused")
                    if response.headers.get("content-type", "").split(";")[0] != "application/json":
                        raise ValueError("Expected JSON")
                    length = response.headers.get("content-length")
                    if length is not None and not 0 <= int(length) <= limit:
                        raise ValueError("Response too large")
                    data = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        if len(data) + len(chunk) > limit:
                            raise ValueError("Response too large")
                        data.extend(chunk)
                    result = json.loads(data)
                    if not isinstance(result, dict):
                        raise ValueError("Expected object")
                    return result
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            # Never include a provider body, private prompt or authorization in errors.
            raise VideoError("Studio Broker response unavailable or invalid; do not resubmit") from exc

    @staticmethod
    def decode(data: dict, field: str, limit: int) -> bytes:
        value = data.get(field)
        if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
            raise VideoError("Invalid Studio artifact size")
        try:
            result = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VideoError("Invalid Studio artifact encoding") from exc
        if not result or len(result) > limit:
            raise VideoError("Invalid Studio artifact size")
        return result

    async def synthesize(self, text: str, *, voice: str, style: str = "", workflow=None):
        try:
            data = await self.request("POST", "/v1/speech", 3 * 1024 * 1024,
                body={"text": text, "voice": voice, "style": style, "workflow": workflow}, seconds=95)
            return SpeechResult(self.decode(data, "audio_base64", 2 * 1024 * 1024),
                                str(data.get("mime_type", "")))
        except VideoError as exc:
            raise SpeechError("Studio speech outcome unavailable; do not resubmit") from exc

    async def submit_video(self, prompt: str) -> str:
        data = await self.request("POST", "/v1/video/jobs", 32 * 1024,
            body={"prompt": prompt, "duration_s": 4, "resolution": "720p",
                  "aspect_ratio": "9:16", "workflow": "studio"})
        value = data.get("job_id")
        if type(value) is not int or not 0 < value < 2**63:
            raise VideoError("Invalid Studio remote receipt")
        return str(value)

    async def read_video(self, remote_id: str) -> VideoResult | None:
        if not remote_id.isascii() or not remote_id.isdigit() or not 0 < int(remote_id) < 2**63:
            raise VideoError("Invalid Studio remote receipt")
        # Ignore poll_url completely: no provider-controlled URL/path or credential forwarding.
        data = await self.request("GET", f"/v1/jobs/{remote_id}", 28 * 1024 * 1024)
        if data.get("status") != "done":
            return None  # pending/failed/unknown never authorizes a new generation
        return VideoResult(self.decode(data, "video_base64", 20 * 1024 * 1024),
                           str(data.get("mime_type", "")))
