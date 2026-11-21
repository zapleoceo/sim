"""One waiter per process for every Broker job the process is waiting on (docs/broker.md §2a).

Instead of polling each job (N jobs = N requests every second), the process keeps ONE batch long
poll open: `POST /v1/jobs/wait {"ids": [...], "wait": 25}`. The Broker answers as soon as any of
the listed jobs is finished, with `{"done": [job payloads], "pending": [ids]}`, or after `wait`
seconds with nothing done. Each finished job resolves the future of whoever awaits it, and the
loop immediately asks again for the rest. 20 concurrent replies = 1 open request.

A Broker without the endpoint (404/405) switches the waiter off for `retry_unavailable_s`; the
callers then fall back to per-job polling (`BrokerHttp.poll`)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx

from sim.ports import PortError

log = logging.getLogger(__name__)

WAIT_PATH = "/v1/jobs/wait"
WAIT_S = 25                     # below nginx / Cloudflare read timeouts
MAX_IDS = 100                   # the Broker's per-request limit
MAX_ERRORS = 5                  # transient failures in a row before every waiter gets an error
ERROR_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0)
RETRY_UNAVAILABLE_S = 600.0     # after a 404/405, try the endpoint again this much later
RETRY_AFTER_ERRORS_S = 60.0     # after repeated failures, poll jobs for this long
UNAVAILABLE_STATUS = frozenset({404, 405})


class WaiterUnavailable(Exception):
    """The Broker has no batch wait endpoint: poll this job instead."""


class WaitTransport(Protocol):
    async def wait_jobs(self, ids: list[str], wait_s: int) -> dict[str, Any]: ...


type JobError = Callable[[str, Mapping[str, Any]], PortError]
type Sleep = Callable[[float], Awaitable[None]]
type Clock = Callable[[], float]


class JobWaiter:
    def __init__(self, transport: WaitTransport, *, job_error: JobError,
                 terminal_failed: frozenset[str], sleep: Sleep = asyncio.sleep,
                 clock: Clock | None = None, wait_s: int = WAIT_S, max_ids: int = MAX_IDS,
                 retry_unavailable_s: float = RETRY_UNAVAILABLE_S) -> None:
        self.transport = transport
        self.job_error = job_error
        self.terminal_failed = terminal_failed
        self.sleep = sleep
        self.clock = clock or (lambda: asyncio.get_running_loop().time())
        self.wait_s = wait_s
        self.max_ids = max_ids
        self.retry_unavailable_s = retry_unavailable_s
        self._pending: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._unavailable_until = 0.0

    @property
    def available(self) -> bool:
        return self.clock() >= self._unavailable_until

    async def wait(self, job_id: str, *, what: str, budget_s: float) -> dict[str, Any]:
        """The finished job payload; a failed job raises its PortError; still pending after
        `budget_s` raises a retryable PortError; no endpoint raises WaiterUnavailable."""
        if not self.available:
            raise WaiterUnavailable
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending.setdefault(job_id, []).append(future)
        self._ensure_loop()
        try:
            return await asyncio.wait_for(asyncio.shield(future), budget_s)
        except TimeoutError as exc:
            raise PortError(f"{what} job {job_id} still pending after {budget_s:g}s",
                            retryable=True) from exc
        finally:
            self._forget(job_id, future)

    def _forget(self, job_id: str, future: asyncio.Future[dict[str, Any]]) -> None:
        waiters = self._pending.get(job_id)
        if waiters and future in waiters:
            waiters.remove(future)
            if not waiters:
                del self._pending[job_id]

    def _ensure_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_running_loop().create_task(
                self._run(), name="broker-job-waiter")

    async def _run(self) -> None:
        errors = 0
        while self._pending:
            ids = list(self._pending)[: self.max_ids]
            try:
                body = await self.transport.wait_jobs(ids, self.wait_s)
            except PortError as exc:
                status = getattr(exc, "status", None)
                if status in UNAVAILABLE_STATUS:
                    log.info("broker batch wait unavailable (%s): polling jobs instead", status)
                    self._unavailable_until = self.clock() + self.retry_unavailable_s
                    self._fail_all(WaiterUnavailable())
                    return
                errors += 1
                if not exc.retryable or errors > MAX_ERRORS:
                    # Never lose a job to a broken wait endpoint: its callers poll instead.
                    log.warning("broker batch wait failed %d times (%s): polling jobs instead",
                                errors, exc)
                    self._unavailable_until = self.clock() + RETRY_AFTER_ERRORS_S
                    self._fail_all(WaiterUnavailable())
                    return
                await self.sleep(ERROR_BACKOFF_S[min(errors, len(ERROR_BACKOFF_S)) - 1])
                continue
            errors = 0
            for job in body.get("done") or []:
                if isinstance(job, Mapping):
                    self._settle(job)

    def _settle(self, job: Mapping[str, Any]) -> None:
        job_id = str(job.get("job_id") or "")
        status = str(job.get("status") or "")
        waiters = self._pending.pop(job_id, [])
        for future in waiters:
            if future.done():
                continue
            if status == "done":
                future.set_result(dict(job))
            elif status in self.terminal_failed:
                future.set_exception(self.job_error(job_id, job))
            else:                                   # not terminal after all: keep waiting
                self._pending.setdefault(job_id, []).append(future)

    def _fail_all(self, exc: BaseException) -> None:
        pending, self._pending = self._pending, {}
        for waiters in pending.values():
            for future in waiters:
                if not future.done():
                    future.set_exception(exc)


def wait_timeout(wait_s: int) -> httpx.Timeout:
    """The read timeout of one batch wait: the Broker holds it up to `wait_s` seconds."""
    return httpx.Timeout(connect=5.0, read=wait_s + 10.0, write=10.0, pool=5.0)
