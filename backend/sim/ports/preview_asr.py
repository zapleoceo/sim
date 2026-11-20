"""Contract boundary for durable ASR; no synchronous fallback."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

MAX_ASR_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class AsrReceipt:
    job_id: str
    status: Literal["pending", "running", "done", "unknown", "error", "cancelled"]
    text: str | None = None


class PreviewAsr(Protocol):
    async def submit(self, key: str, data: bytes, filename: str) -> AsrReceipt: ...
    async def by_key(self, key: str) -> AsrReceipt | None: ...
    async def status(self, job_id: str) -> AsrReceipt: ...
