"""LLM port. The only implementation is the AIbroker client — no provider keys in Sim."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str | None = None
    provider: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0
    job_id: str | None = None


class LLMError(Exception):
    """The broker answered with an error or the job did not finish in budget."""


class LLMPort(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        capability: str = "chat:sales",
        model: str | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
        workflow: str | None = None,
    ) -> LLMResult: ...

    async def transcribe(self, audio: bytes, filename: str) -> LLMResult: ...

    async def list_models(self) -> list[dict[str, Any]]: ...
