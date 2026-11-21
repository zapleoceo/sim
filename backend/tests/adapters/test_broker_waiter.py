"""The per-process batch waiter: many jobs, one long poll; polling only as a fallback."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx

from sim.adapters.broker.http import FAILED_JOB_STATES, BrokerHttp, job_error
from sim.adapters.broker.waiter import JobWaiter, WaiterUnavailable
from sim.ports import PortError
from tests.adapters.helpers import BASE_URL, PROJECT_KEY, done, submitted


async def no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


class ScriptedTransport:
    """Answers each batch wait from a script; records the ids asked for."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.asked: list[list[str]] = []

    async def wait_jobs(self, ids: list[str], wait_s: int) -> dict[str, Any]:
        self.asked.append(list(ids))
        await asyncio.sleep(0)
        answer = self.answers.pop(0) if self.answers else {"done": [], "pending": ids}
        if isinstance(answer, Exception):
            raise answer
        return answer


def waiter(transport: ScriptedTransport, **kw: Any) -> JobWaiter:
    return JobWaiter(transport, job_error=lambda job_id, job: job_error("job", job_id, job),
                     terminal_failed=FAILED_JOB_STATES, sleep=no_sleep, **kw)


def job(job_id: str, status: str = "done", **extra: Any) -> dict[str, Any]:
    return {"job_id": job_id, "status": status, **extra}


async def test_many_jobs_share_one_long_poll() -> None:
    transport = ScriptedTransport({"done": [job("1", text="a"), job("2", text="b")]})
    w = waiter(transport)
    first, second = await asyncio.gather(w.wait("1", what="chat", budget_s=5),
                                         w.wait("2", what="chat", budget_s=5))
    assert (first["text"], second["text"]) == ("a", "b")
    assert transport.asked == [["1", "2"]]


async def test_a_job_finishing_later_keeps_the_others_waiting() -> None:
    transport = ScriptedTransport({"done": [job("1")], "pending": ["2"]},
                                  {"done": [], "pending": ["2"]},
                                  {"done": [job("2")]})
    w = waiter(transport)
    await asyncio.gather(w.wait("1", what="chat", budget_s=5),
                         w.wait("2", what="chat", budget_s=5))
    assert transport.asked == [["1", "2"], ["2"], ["2"]]


async def test_a_failed_job_raises_its_error() -> None:
    transport = ScriptedTransport({"done": [job("1", "error", error="model_unavailable")]})
    with pytest.raises(PortError, match="model_unavailable"):
        await waiter(transport).wait("1", what="chat", budget_s=5)


async def test_still_pending_after_the_budget_is_retryable() -> None:
    w = waiter(ScriptedTransport())                       # every wait: nothing done yet
    with pytest.raises(PortError, match="still pending") as info:
        await w.wait("1", what="chat", budget_s=0.05)
    assert info.value.retryable is True


async def test_no_endpoint_switches_to_polling_for_a_while() -> None:
    missing = PortError("not found")
    missing.status = 404  # type: ignore[attr-defined]
    now = [0.0]
    w = waiter(ScriptedTransport(missing), clock=lambda: now[0], retry_unavailable_s=600)
    with pytest.raises(WaiterUnavailable):
        await w.wait("1", what="chat", budget_s=5)
    assert w.available is False
    now[0] = 601.0
    assert w.available is True


async def test_repeated_failures_fall_back_to_polling_instead_of_losing_jobs() -> None:
    flaky = [PortError("502", retryable=True) for _ in range(6)]
    w = waiter(ScriptedTransport(*flaky))
    with pytest.raises(WaiterUnavailable):
        await w.wait("1", what="chat", budget_s=5)


# --- BrokerHttp with the waiter --------------------------------------------------------------


async def test_submit_waits_through_the_batch_endpoint_not_polling() -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as broker:
        broker.post("/v1/jobs").mock(side_effect=[submitted(7), submitted(8)])

        def answer(request: httpx.Request) -> httpx.Response:
            ids = json.loads(request.content)["ids"]
            return httpx.Response(200, json={"done": [{"job_id": i, "status": "done",
                                                       "text": f"t{i}"} for i in ids]})

        wait = broker.post("/v1/jobs/wait").mock(side_effect=answer)
        poll = broker.get(path__regex=r"/v1/jobs/\d+")
        http = BrokerHttp(BASE_URL, PROJECT_KEY, batch_wait=True)
        try:
            outcomes = await asyncio.gather(
                http.submit("/v1/jobs", {}, what="chat structured", budget_s=30),
                http.submit("/v1/jobs", {}, what="chat structured", budget_s=30))
        finally:
            await http.aclose()
    assert sorted(o.data["text"] for o in outcomes) == ["t7", "t8"]
    assert 1 <= wait.call_count <= 2
    assert poll.call_count == 0
    assert json.loads(wait.calls[0].request.content)["wait"] == 25


async def test_without_the_endpoint_the_job_is_polled() -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as broker:
        broker.post("/v1/jobs").mock(return_value=submitted(7))
        broker.post("/v1/jobs/wait").mock(return_value=httpx.Response(404))
        broker.get("/v1/jobs/7").mock(return_value=done(text="polled"))
        http = BrokerHttp(BASE_URL, PROJECT_KEY, batch_wait=True, sleep=no_sleep)
        try:
            outcome = await http.submit("/v1/jobs", {}, what="chat", budget_s=30)
        finally:
            await http.aclose()
    assert outcome.data["text"] == "polled"
    assert http.waiter is not None and http.waiter.available is False


async def test_a_lane_limits_jobs_in_flight() -> None:
    http = BrokerHttp(BASE_URL, PROJECT_KEY, inflight={"chat": 1})
    inside: list[int] = []
    peak = 0

    async def job_in_lane() -> None:
        nonlocal peak
        async with http.slot("chat"):
            inside.append(1)
            peak = max(peak, len(inside))
            await asyncio.sleep(0.01)
            inside.pop()

    await asyncio.gather(*(job_in_lane() for _ in range(5)))
    assert peak == 1
    assert http.inflight["video"] == 2          # defaults stay for the other lanes
