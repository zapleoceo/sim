"""Project-scoped transport for the typed durable Broker ASR contract."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

import httpx

from sim.ports.llm import LLMError
from sim.ports.preview_asr import MAX_ASR_UPLOAD_BYTES, AsrReceipt

PATH_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
MAX_TEXT_BYTES = 256 * 1024
# JSON may escape each ASCII character as six bytes.
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
CONTRACT_HEADER = "X-Broker-Job-Contract"
CONTRACT = "transcription-keyed-v1"
ASR_PATH = "/v1/transcribe/jobs"
ASR_SUBMIT_PATH = "/v1/transcribe/keyed-jobs"


@dataclass(frozen=True)
class BrokerPreviewAsr:
    origin: str
    project_key: str
    client: Callable[[], httpx.AsyncClient]

    def __post_init__(self) -> None:
        url = httpx.URL(self.origin)
        if (url.scheme != "https" or not url.host or url.userinfo
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("Invalid Broker ASR origin")

    async def submit(self, key: str, data: bytes, filename: str) -> AsrReceipt:
        self.validate_token(key)
        if not 0 < len(data) <= MAX_ASR_UPLOAD_BYTES:
            raise LLMError("Invalid ASR upload size")
        if not filename or len(filename) > 120 or any(c in filename for c in ("/", "\\", "\r", "\n", "\x00")):
            raise LLMError("Invalid ASR upload filename")
        probe_key = f"preview-asr-{int(time.time() * 1000)}-{uuid4()}"
        if await self.by_key(probe_key) is not None:
            raise LLMError("ASR compatibility probe did not return an unused key")
        receipt = await self.request(
            "POST", ASR_SUBMIT_PATH, key=key, files={"file": (filename, data, "application/octet-stream")},
        )
        if receipt is None:
            raise LLMError("ASR submission receipt unavailable")
        return receipt

    async def by_key(self, key: str) -> AsrReceipt | None:
        self.validate_token(key)
        return await self.request("GET", f"{ASR_PATH}/by-key/{key}", allow_missing=True)

    async def status(self, job_id: str) -> AsrReceipt:
        self.validate_token(job_id)
        if not job_id.isdigit() or job_id.startswith("0") or len(job_id) > 19 or int(job_id) > 2**63 - 1:
            raise LLMError("Invalid ASR job ID")
        receipt = await self.request("GET", f"{ASR_PATH}/{job_id}")
        if receipt is None or receipt.job_id != job_id:
            raise LLMError("ASR receipt identity mismatch")
        return receipt

    async def request(
        self, method: str, path: str, *, key: str | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None, allow_missing: bool = False,
    ) -> AsrReceipt | None:
        headers = {"X-Project-Key": self.project_key, "Accept": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            async with asyncio.timeout(8):
                async with self.client().stream(
                    method, self.origin.rstrip("/") + path, headers=headers,
                    params={"workflow": "preview"} if method == "POST" else None,
                    files=files, follow_redirects=False,
                ) as response:
                    if allow_missing and response.status_code == 404:
                        if response.headers.get(CONTRACT_HEADER) != CONTRACT:
                            raise LLMError("Broker ASR contract is not verified")
                        return None
                    if response.status_code not in ((200, 202) if method == "POST" else (200,)):
                        raise LLMError("ASR receipt unavailable")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise LLMError("Invalid ASR receipt content type")
                    if method == "POST" and response.headers.get(CONTRACT_HEADER) != CONTRACT:
                        raise LLMError("ASR acceptance contract is not verified")
                    status_code = response.status_code
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                        if len(data) + len(chunk) > MAX_RECEIPT_BYTES:
                            raise LLMError("ASR receipt exceeds size limit")
                        data.extend(chunk)
            payload = json.loads(data, object_pairs_hook=unique_object)
            if not isinstance(payload, dict):
                raise ValueError("ASR receipt is not an object")
            return decode_receipt(payload, fresh=status_code == 202)
        except (httpx.HTTPError, TimeoutError, TypeError, ValueError, KeyError, RecursionError, OverflowError) as exc:
            raise LLMError("ASR receipt unavailable or invalid") from exc

    @staticmethod
    def validate_token(value: str) -> None:
        if not isinstance(value, str) or PATH_TOKEN.fullmatch(value) is None:
            raise LLMError("Invalid ASR identifier")


def decode_receipt(payload: dict[str, Any], *, fresh: bool) -> AsrReceipt:
    job_id = payload["job_id"]
    if type(job_id) is not int or not 0 < job_id <= 2**63 - 1:
        raise ValueError("Invalid ASR job ID")
    if not isinstance(payload["poll_url"], str):
        raise ValueError("Invalid ASR poll URL field")
    poll_after = payload["poll_after_s"]
    if poll_after is not None and not (
        type(poll_after) in (int, float) and math.isfinite(poll_after) and poll_after >= 0
    ):
        raise ValueError("Invalid ASR poll interval")
    if fresh:
        if payload.get("status", "pending") != "pending" or poll_after != 2:
            raise ValueError("Invalid ASR acceptance receipt")
        return AsrReceipt(str(job_id), "pending")
    status: Literal["pending", "running", "done", "error", "cancelled"] = payload["status"]
    if status not in ("pending", "running", "done", "error", "cancelled"):
        raise ValueError("Invalid ASR status")
    text = payload["text"]
    if text is not None and (not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES):
        raise ValueError("Invalid ASR transcript")
    if status == "done" and text is None:
        raise ValueError("Missing ASR transcript")
    for field in ("error_code", "provider", "model", "model_served"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError("Invalid ASR metadata")
    for field in ("cost_usd", "latency_ms"):
        value = payload.get(field)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError("Invalid ASR metadata")
    return AsrReceipt(str(job_id), status, text if status == "done" else None)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate ASR receipt field")
        result[key] = value
    return result
