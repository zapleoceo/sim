"""Shared plumbing of every AI Broker adapter: one pooled httpx client with the project key,
submit + poll of queued jobs, and the single mapping of failures to `PortError(retryable=...)`.

Retry rule (docs/broker.md): a failure a later attempt can get past is retryable (network,
timeouts, 408/425/429/5xx, a job still pending after the budget, a worker restart); a request
the Broker refused for what it is (4xx, a failed or cancelled job, a spent budget cap) is not."""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from sim.adapters.broker.waiter import WAIT_PATH, JobWaiter, WaiterUnavailable, wait_timeout
from sim.config import Settings
from sim.ports import PortError, Usage

log = logging.getLogger(__name__)

SUBSCRIPTION_PREFIXES = ("claude-sub/", "codex-sub/")
USER_AGENT = "sim-broker/2.0"
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
POLL_MAX_ERRORS = 5             # consecutive transient poll failures tolerated per job
DEFAULT_POLL_AFTER_S = 2.0
MAX_POLL_SLEEP_S = 15.0
# A chat reply is usually ready in 3-10 s, while the Broker suggests polling every 5-10 s: early
# on, poll every second so a finished answer is not left waiting; later honour the Broker's pace.
FAST_POLL_S = 1.0
FAST_POLL_WINDOW_S = 90.0
ERROR_TEXT_LIMIT = 300
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# Job error codes (`error_code` on a failed poll) that a fresh attempt can get past.
RETRYABLE_JOB_CODES = frozenset({"worker_restarted", "capacity_busy", "auth_refresh_failed"})
# A spent daily / monthly / lifetime allowance: retrying before the reset only burns attempts.
BUDGET_MARKERS = ("budget cap reached", "request_cap_exhausted")
FAILED_JOB_STATES = frozenset({"error", "cancelled", "unknown"})
# How many Broker jobs of one lane this process runs at once (more wait here, not in the
# Broker or at the provider's rate limit). The lane is the first word of `what`.
DEFAULT_INFLIGHT: dict[str, int] = {"default": 8, "chat": 12, "transcribe": 4, "speech": 4,
                                    "image": 3, "video": 2}

type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], float]


def poll_sleep(suggested_s: float, elapsed_s: float) -> float:
    """Seconds to wait before the next poll: at most FAST_POLL_S during the first
    FAST_POLL_WINDOW_S of a job, then the Broker's suggestion capped at MAX_POLL_SLEEP_S."""
    cap = FAST_POLL_S if elapsed_s < FAST_POLL_WINDOW_S else MAX_POLL_SLEEP_S
    return min(suggested_s, cap)


class BrokerHttpError(PortError):
    """A Broker answer with an HTTP error status (`status`) or no answer at all (`status` None)."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message, retryable=retryable)
        self.status = status


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    data: dict[str, Any]        # the finished poll body
    latency_ms: int


def is_subscription_model(model: str) -> bool:
    return model.startswith(SUBSCRIPTION_PREFIXES)


def is_budget_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BUDGET_MARKERS)


def http_error(what: str, exc: httpx.HTTPError) -> BrokerHttpError:
    """The one place that turns an httpx failure into a port error."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text[:ERROR_TEXT_LIMIT]
        retryable = status in RETRYABLE_STATUS and not is_budget_error(body)
        return BrokerHttpError(f"{what}: HTTP {status}: {body}", retryable=retryable,
                               status=status)
    # Connect / read / write timeouts and dropped connections: the next attempt may pass.
    return BrokerHttpError(f"{what}: {type(exc).__name__}: {exc}"[:ERROR_TEXT_LIMIT],
                           retryable=True)


def job_error(what: str, job_id: str, data: Mapping[str, Any]) -> PortError:
    status = str(data.get("status") or "error")
    code = str(data.get("error_code") or "")
    text = str(data.get("error") or "")[:ERROR_TEXT_LIMIT]
    retryable = code in RETRYABLE_JOB_CODES and not is_budget_error(text)
    reason = f"{code}: {text}" if code and text else (code or text or "no reason given")
    return PortError(f"{what} job {job_id} {status}: {reason}", retryable=retryable)


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def pick(data: Mapping[str, Any], *names: str) -> Any:
    """First non-None value among the body and its `result_meta` (older jobs keep it there)."""
    meta = data.get("result_meta")
    for source in (data, meta if isinstance(meta, Mapping) else {}):
        for name in names:
            if source.get(name) is not None:
                return source[name]
    return None


def usage_of(data: Mapping[str, Any], *, model: str, latency_ms: int,
             job_id: str | None) -> Usage:
    """Cost, tokens and model of a finished Broker call (job poll or synchronous answer)."""
    openai_usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
    tokens_in = as_int(pick(data, "tokens_in"))
    tokens_out = as_int(pick(data, "tokens_out"))
    return Usage(
        model=str(pick(data, "model") or model),
        cost_usd=as_float(pick(data, "cost_usd")),
        tokens_in=tokens_in if tokens_in is not None
        else as_int(openai_usage.get("prompt_tokens")),
        tokens_out=tokens_out if tokens_out is not None
        else as_int(openai_usage.get("completion_tokens")),
        latency_ms=latency_ms,
        job_id=job_id,
    )


def decode_b64(value: Any, *, what: str) -> bytes:
    """Base64 media from a finished answer; a missing or broken field is a non-retryable error
    (the call was made and billed, repeating it blindly would bill it again)."""
    if not isinstance(value, str) or not value:
        raise PortError(f"{what}: the answer has no media")
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as exc:       # binascii.Error is a ValueError
        raise PortError(f"{what}: the media is not valid base64") from exc
    if not data:
        raise PortError(f"{what}: the answer has no media")
    return data


def encode_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class BrokerHttp:
    """HTTP access to the Broker. One instance is shared by every port adapter (one connection
    pool); `aclose()` releases it at shutdown."""

    def __init__(self, base_url: str, project_key: str, *, poll_budget_s: float = 300.0,
                 media_budget_s: float = 900.0, client: httpx.AsyncClient | None = None,
                 sleep: Sleep = asyncio.sleep, clock: Clock = time.monotonic,
                 batch_wait: bool = False,
                 inflight: Mapping[str, int] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        self.poll_budget_s = poll_budget_s      # chat, transcription
        self.media_budget_s = media_budget_s    # video, images, subscription models
        self.sleep = sleep
        self.clock = clock
        self._client = client
        # One batch long poll for every job of this process (waiter.py); None = poll each job.
        self.waiter = JobWaiter(
            self, job_error=lambda job_id, job: job_error("job", job_id, job),
            terminal_failed=FAILED_JOB_STATES, sleep=sleep, clock=clock,
        ) if batch_wait else None
        self.inflight = {**DEFAULT_INFLIGHT, **(inflight or {})}
        self._lanes: dict[str, asyncio.Semaphore] = {}

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> BrokerHttp:
        return cls(settings.broker_url, settings.broker_project_key,
                   poll_budget_s=settings.broker_poll_budget_s,
                   media_budget_s=settings.broker_video_budget_s,
                   batch_wait=settings.broker_batch_wait,
                   inflight=settings.broker_max_inflight, **kwargs)

    @asynccontextmanager
    async def slot(self, lane: str) -> AsyncIterator[None]:
        """Hold one of the lane's in-flight places for the whole job (submit + wait)."""
        semaphore = self._lanes.get(lane)
        if semaphore is None:
            limit = self.inflight.get(lane, self.inflight["default"])
            semaphore = self._lanes[lane] = asyncio.Semaphore(max(1, limit))
        async with semaphore:
            yield

    async def wait_jobs(self, ids: list[str], wait_s: int) -> dict[str, Any]:
        """One batch long poll (the waiter's transport)."""
        return await self.json("POST", WAIT_PATH, what="wait", json={"ids": ids, "wait": wait_s},
                               http_timeout=wait_timeout(wait_s))

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {"X-Project-Key": self.project_key, "User-Agent": USER_AGENT}
        if extra:
            headers.update(extra)
        return headers

    def elapsed_ms(self, start: float) -> int:
        return max(0, int((self.clock() - start) * 1000))

    async def request(self, method: str, path: str, *, what: str,
                      params: Mapping[str, str] | None = None, json: Any = None,
                      files: Mapping[str, Any] | None = None,
                      headers: Mapping[str, str] | None = None,
                      http_timeout: httpx.Timeout | None = None) -> httpx.Response:
        """One call; any transport failure or non-2xx status becomes a `BrokerHttpError`."""
        kwargs: dict[str, Any] = {"params": params, "headers": self.headers(headers)}
        if json is not None:
            kwargs["json"] = json
        if files is not None:
            kwargs["files"] = files
        if http_timeout is not None:
            kwargs["timeout"] = http_timeout
        try:
            response = await self.client.request(method, f"{self.base_url}{path}", **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise http_error(what, exc) from exc
        return response

    @staticmethod
    def decode(response: httpx.Response, *, what: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:   # a proxy page instead of JSON: worth another attempt
            raise PortError(f"{what}: the answer is not JSON", retryable=True) from exc
        if not isinstance(body, dict):
            raise PortError(f"{what}: unexpected answer shape")
        return body

    async def json(self, method: str, path: str, *, what: str, **kwargs: Any) -> dict[str, Any]:
        return self.decode(await self.request(method, path, what=what, **kwargs), what=what)

    async def submit(self, path: str, body: Mapping[str, Any], *, what: str, budget_s: float,
                     params: Mapping[str, str] | None = None) -> JobOutcome:
        """POST a queued job, then wait until it is done (within the lane's in-flight limit)."""
        async with self.slot(what.split()[0]):
            start = self.clock()
            job = await self.json("POST", path, what=what, params=params, json=body)
            data = await self.poll(job, what=what, budget_s=budget_s)
            return JobOutcome(job_id=str(job.get("job_id")), data=data,
                              latency_ms=self.elapsed_ms(start))

    async def poll(self, job: Mapping[str, Any], *, what: str, budget_s: float,
                   default_path: str | None = None) -> dict[str, Any]:
        """Poll a submitted job (its `poll_url`, else `default_path`, else `/v1/jobs/{id}`) until
        `done`. A failed job raises its error; a job still running after `budget_s` and more than
        POLL_MAX_ERRORS transient poll failures in a row raise a retryable error."""
        job_id = str(job.get("job_id") or "")
        if not job_id:
            raise PortError(f"{what}: the Broker returned no job id")
        status = str(job.get("status") or "pending")
        if status == "done":                    # an idempotent replay of a finished job
            return dict(job)
        if status in FAILED_JOB_STATES:
            raise job_error(what, job_id, job)
        path = str(job.get("poll_url") or default_path or f"/v1/jobs/{job_id}")
        if self.waiter is not None and path == f"/v1/jobs/{job_id}":
            try:
                return await self.waiter.wait(job_id, what=what, budget_s=budget_s)
            except WaiterUnavailable:
                pass                                    # the Broker has no batch wait: poll
        wait = as_float(job.get("poll_after_s")) or DEFAULT_POLL_AFTER_S
        started = self.clock()
        deadline = started + budget_s
        errors = 0
        while True:
            await self.sleep(poll_sleep(wait, self.clock() - started))
            try:
                data = await self.json("GET", path, what=what)
            except PortError as exc:
                if not exc.retryable:
                    raise
                errors += 1
                log.warning("%s poll error %d/%d job=%s: %s", what, errors, POLL_MAX_ERRORS,
                            job_id, exc)
                if errors > POLL_MAX_ERRORS:
                    raise PortError(f"{what} job {job_id}: {errors} poll errors in a row: {exc}",
                                    retryable=True) from exc
            else:
                errors = 0
                status = str(data.get("status") or "pending")
                if status == "done":
                    return data
                if status in FAILED_JOB_STATES:
                    raise job_error(what, job_id, data)
                wait = as_float(data.get("poll_after_s")) or wait
            if self.clock() >= deadline:
                raise PortError(f"{what} job {job_id} still {status} after {budget_s:g}s",
                                retryable=True)
