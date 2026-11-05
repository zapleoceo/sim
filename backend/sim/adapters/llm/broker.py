"""AIbroker client: implements ports.llm.LLMPort over the broker's async job API.

chat():       POST {url}/v1/jobs?capability=X  ->  poll GET /v1/jobs/{id} until done / error.
transcribe(): POST {url}/v1/transcribe/jobs (multipart file) -> same poll.
list_models():GET  {url}/v1/models.

Subscription models (claude-sub/*, codex-sub/*) reject non-default max_tokens/temperature, so
those fields are never sent for them."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from sim.ports.llm import LLMError, LLMResult

log = logging.getLogger(__name__)

SUBSCRIPTION_PREFIXES = ("claude-sub/", "codex-sub/")
JOB_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
# Without max_tokens the broker applies its own default (1024), which truncates JSON decisions.
DEFAULT_MAX_TOKENS = 2048
POLL_MAX_ERRORS = 5  # consecutive transient poll failures tolerated per job
DEFAULT_POLL_AFTER_S = 2.0
MAX_POLL_SLEEP_S = 15.0


def is_subscription_model(model: str | None) -> bool:
    return bool(model) and model.startswith(SUBSCRIPTION_PREFIXES)  # type: ignore[union-attr]


def err_text(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
    return f"{type(exc).__name__}: {exc}"[:400]


def pick(data: dict[str, Any], meta: dict[str, Any], *names: str) -> Any:
    """First non-None value among the poll body and its `result_meta`."""
    for source in (data, meta):
        for name in names:
            if source.get(name) is not None:
                return source[name]
    return None


def finish_reason_of(data: dict[str, Any], meta: dict[str, Any]) -> str | None:
    """`finish_reason` from the poll body, its result_meta or an OpenAI-style choices[0]."""
    value = pick(data, meta, "finish_reason")
    if value is None:
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            value = choices[0].get("finish_reason")
    return str(value)[:32] if value is not None else None


def to_result(data: dict[str, Any], *, latency_ms: int, job_id: str | None) -> LLMResult:
    raw_meta = data.get("result_meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost = pick(data, meta, "cost_usd")
    return LLMResult(
        text=(data.get("text") or data.get("transcript") or meta.get("text") or ""),
        model=pick(data, meta, "model"),
        provider=pick(data, meta, "provider"),
        tokens_in=pick(data, meta, "tokens_in") if pick(data, meta, "tokens_in") is not None
        else usage.get("prompt_tokens"),
        tokens_out=pick(data, meta, "tokens_out") if pick(data, meta, "tokens_out") is not None
        else usage.get("completion_tokens"),
        cached_tokens=pick(data, meta, "cache_read_tokens", "cached_tokens"),
        cost_usd=float(cost) if cost is not None else None,
        latency_ms=latency_ms,
        job_id=job_id,
        finish_reason=finish_reason_of(data, meta),
    )


class BrokerLLM:
    def __init__(
        self,
        base_url: str,
        project_key: str,
        *,
        poll_budget_s: float = 180.0,
        subscription_budget_s: float = 600.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.url = base_url.rstrip("/")
        self.key = project_key
        self.poll_budget_s = poll_budget_s
        self.subscription_budget_s = subscription_budget_s
        self.sleep = sleep
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """One pooled client per adapter, created lazily (keep-alive to the broker across calls)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=JOB_HTTP_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Project-Key": self.key}

    def budget_for(self, model: str | None) -> float:
        return self.subscription_budget_s if is_subscription_model(model) else self.poll_budget_s

    # ---- LLMPort -------------------------------------------------------------------------
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
    ) -> LLMResult:
        body: dict[str, Any] = {"messages": messages}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if model:
            body["model"] = model
        if not is_subscription_model(model):
            body["max_tokens"] = max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
            if temperature is not None:
                body["temperature"] = temperature
        params = {"capability": capability}
        if workflow:
            params["workflow"] = workflow
            body["workflow"] = workflow
        start = time.perf_counter()
        try:
            client = self.client
            resp = await client.post(
                f"{self.url}/v1/jobs", params=params, headers=self.headers, json=body
            )
            resp.raise_for_status()
            job = resp.json()
            data = await self.poll(client, job, capability, start + self.budget_for(model))
        except LLMError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
            raise LLMError(f"{capability}: {err_text(exc)}") from exc
        return to_result(
            data, latency_ms=elapsed_ms(start), job_id=str(job.get("job_id", ""))
        )

    async def transcribe(self, audio: bytes, filename: str) -> LLMResult:
        start = time.perf_counter()
        try:
            client = self.client
            files = {"file": (filename, audio, "application/octet-stream")}
            resp = await client.post(
                f"{self.url}/v1/transcribe/jobs",
                params={"workflow": "voice"},
                headers=self.headers,
                files=files,
            )
            if resp.status_code in (404, 405):  # older broker: synchronous endpoint only
                resp = await client.post(
                    f"{self.url}/v1/transcribe",
                    params={"workflow": "voice"},
                    headers=self.headers,
                    files=files,
                )
            resp.raise_for_status()
            job = resp.json()
            data = job
            if job.get("job_id") is not None:
                data = await self.poll(client, job, "audio", start + self.poll_budget_s)
        except LLMError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TimeoutError) as exc:
            raise LLMError(f"audio: {err_text(exc)}") from exc
        return to_result(data, latency_ms=elapsed_ms(start), job_id=str(job.get("job_id") or ""))

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            resp = await self.client.get(f"{self.url}/v1/models", headers=self.headers)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"models: {err_text(exc)}") from exc
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        return [r for r in rows if isinstance(r, dict)]

    # ---- polling -------------------------------------------------------------------------
    async def poll(
        self, client: httpx.AsyncClient, job: dict[str, Any], what: str, deadline: float
    ) -> dict[str, Any]:
        job_id = job["job_id"]
        url = f"{self.url}{job.get('poll_url') or f'/v1/jobs/{job_id}'}"
        wait = float(job.get("poll_after_s") or DEFAULT_POLL_AFTER_S)
        errors = 0
        while True:
            try:
                resp = await client.get(url, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
                errors = 0
            except (httpx.HTTPError, ValueError) as exc:
                errors += 1
                log.warning("%s poll error %d/%d job=%s: %s", what, errors, POLL_MAX_ERRORS,
                            job_id, err_text(exc))
                if errors > POLL_MAX_ERRORS:
                    raise LLMError(f"{what} job {job_id}: {errors} poll errors") from exc
                data = None
            if data is not None:
                status = data.get("status")
                if status == "done":
                    return data
                if status in ("error", "cancelled"):
                    raise LLMError(f"{what} job {job_id} {status}: {data.get('error')}")
                wait = float(data.get("poll_after_s") or wait)
            if time.perf_counter() > deadline:
                raise LLMError(f"{what} job {job_id} still pending after budget")
            await self.sleep(min(wait, MAX_POLL_SLEEP_S))


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
