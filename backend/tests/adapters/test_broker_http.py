"""BrokerHttp: submit + poll, the poll budget, and the failure → PortError(retryable) mapping."""
from __future__ import annotations

import httpx
import pytest
import respx

from sim.adapters.broker.http import (
    POLL_MAX_ERRORS,
    BrokerHttp,
    BrokerHttpError,
    decode_b64,
    is_subscription_model,
    usage_of,
)
from sim.config import Settings
from sim.ports import PortError
from tests.adapters.helpers import PROJECT_KEY, FakeTime, done, failed, pending, submitted


async def test_submit_polls_until_done(http: BrokerHttp, broker: respx.MockRouter,
                                       fake_time: FakeTime) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    poll = broker.get("/v1/jobs/7").mock(side_effect=[pending(poll_after_s=5),
                                                      done(text="hi")])
    outcome = await http.submit("/v1/jobs", {"messages": []}, what="chat", budget_s=60,
                                params={"capability": "chat:smart"})
    assert outcome.job_id == "7"
    assert outcome.data["text"] == "hi"
    assert fake_time.sleeps == [2, 5]
    assert outcome.latency_ms == 7000
    assert poll.call_count == 2
    request = post.calls.last.request
    assert request.headers["X-Project-Key"] == PROJECT_KEY
    assert request.url.params["capability"] == "chat:smart"


async def test_poll_sleep_is_capped(http: BrokerHttp, broker: respx.MockRouter,
                                    fake_time: FakeTime) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(side_effect=[pending(poll_after_s=60), done()])
    await http.submit("/v1/jobs", {}, what="chat", budget_s=600)
    assert fake_time.sleeps == [2, 15]


async def test_done_replay_needs_no_poll(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=done(text="cached"))
    poll = broker.get("/v1/jobs/7")
    outcome = await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert outcome.data["text"] == "cached"
    assert poll.call_count == 0


async def test_failed_job_is_not_retryable(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=failed(error="InvalidJSON from every provider"))
    with pytest.raises(PortError, match="InvalidJSON") as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert info.value.retryable is False


async def test_worker_restart_is_retryable(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=failed(error="stale",
                                                      error_code="worker_restarted"))
    with pytest.raises(PortError, match="worker_restarted") as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert info.value.retryable is True


async def test_budget_cap_job_error_is_not_retryable(http: BrokerHttp,
                                                     broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=failed(
        error="daily budget cap reached — retry after 00:00 UTC", error_code="capacity_busy"))
    with pytest.raises(PortError) as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert info.value.retryable is False


async def test_cancelled_job_raises(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=httpx.Response(
        200, json={"job_id": 7, "status": "cancelled"}))
    with pytest.raises(PortError, match="cancelled"):
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)


async def test_budget_runs_out_retryable(http: BrokerHttp, broker: respx.MockRouter,
                                         fake_time: FakeTime) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    poll = broker.get("/v1/jobs/7").mock(return_value=pending(poll_after_s=10))
    with pytest.raises(PortError, match="still pending after 30s") as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=30)
    assert info.value.retryable is True
    assert poll.call_count == 4          # t = 2, 12, 22, 32 s
    assert fake_time.now - 1000.0 >= 30


async def test_transient_poll_errors_are_tolerated(http: BrokerHttp,
                                                   broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(side_effect=[httpx.Response(503, text="busy"),
                                               httpx.ReadTimeout,
                                               done(text="ok")])
    outcome = await http.submit("/v1/jobs", {}, what="chat", budget_s=600)
    assert outcome.data["text"] == "ok"


async def test_too_many_poll_errors_are_retryable(http: BrokerHttp,
                                                  broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    poll = broker.get("/v1/jobs/7").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(PortError, match="poll errors in a row") as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=600)
    assert info.value.retryable is True
    assert poll.call_count == POLL_MAX_ERRORS + 1


async def test_poll_404_stops_at_once(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    poll = broker.get("/v1/jobs/7").mock(return_value=httpx.Response(404, text="no job"))
    with pytest.raises(BrokerHttpError) as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=600)
    assert info.value.status == 404
    assert info.value.retryable is False
    assert poll.call_count == 1


async def test_poll_follows_poll_url(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/video/jobs").mock(return_value=submitted(5, poll_url="/v1/custom/5"))
    poll = broker.get("/v1/custom/5").mock(return_value=done(5))
    await http.submit("/v1/video/jobs", {}, what="video", budget_s=60)
    assert poll.call_count == 1


async def test_submit_without_job_id(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=httpx.Response(202, json={"status": "pending"}))
    with pytest.raises(PortError, match="no job id"):
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)


@pytest.mark.parametrize(("response", "retryable"), [
    (httpx.Response(422, json={"detail": "bad body"}), False),
    (httpx.Response(400, text="model not served"), False),
    (httpx.Response(403, text="scope"), False),
    (httpx.Response(429, text="slow down"), True),
    (httpx.Response(429, json={"error": "request_cap_exhausted"}), False),
    (httpx.Response(502, text="daily budget cap reached — retry after 00:00 UTC"), False),
    (httpx.Response(503, text="no capacity"), True),
    (httpx.Response(504, text="gateway timeout"), True),
])
async def test_http_status_mapping(http: BrokerHttp, broker: respx.MockRouter,
                                   response: httpx.Response, retryable: bool) -> None:
    broker.post("/v1/jobs").mock(return_value=response)
    with pytest.raises(BrokerHttpError) as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert info.value.retryable is retryable
    assert info.value.status == response.status_code


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout,
                                   httpx.RemoteProtocolError])
async def test_transport_errors_are_retryable(http: BrokerHttp, broker: respx.MockRouter,
                                              error: type[Exception]) -> None:
    broker.post("/v1/jobs").mock(side_effect=error)
    with pytest.raises(BrokerHttpError) as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert info.value.retryable is True
    assert info.value.status is None


async def test_non_json_answer_is_retryable(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    with pytest.raises(PortError, match="not JSON") as info:
        await http.submit("/v1/jobs", {}, what="chat", budget_s=60)
    assert info.value.retryable is True


async def test_list_answer_is_rejected(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.get("/v1/models").mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(PortError, match="unexpected answer"):
        await http.json("GET", "/v1/models", what="models")


def test_usage_reads_body_meta_and_openai_usage() -> None:
    usage = usage_of({"model": "gemini/flash", "cost_usd": "0.0012", "tokens_in": 10,
                      "result_meta": {"tokens_out": 5, "cost_usd": 9}},
                     model="pinned", latency_ms=50, job_id="3")
    assert usage.model == "gemini/flash"
    assert usage.cost_usd == pytest.approx(0.0012)
    assert (usage.tokens_in, usage.tokens_out) == (10, 5)
    assert (usage.latency_ms, usage.job_id) == (50, "3")
    fallback = usage_of({"usage": {"prompt_tokens": 7, "completion_tokens": 2},
                         "cost_usd": True}, model="pinned", latency_ms=1, job_id=None)
    assert fallback.model == "pinned"
    assert fallback.cost_usd is None
    assert (fallback.tokens_in, fallback.tokens_out) == (7, 2)


def test_decode_b64() -> None:
    assert decode_b64("aGk=", what="x") == b"hi"
    for bad in (None, "", "not base64!!", 5):
        with pytest.raises(PortError) as info:
            decode_b64(bad, what="x")
        assert info.value.retryable is False


def test_subscription_models() -> None:
    assert is_subscription_model("claude-sub/claude-sonnet-5-5")
    assert is_subscription_model("codex-sub/gpt-6.2")
    assert not is_subscription_model("gemini/gemini-3.5-flash")
    assert not is_subscription_model("")


async def test_from_settings_and_lazy_client() -> None:
    settings = Settings(broker_url="https://b.example/", broker_project_key="k",
                        broker_poll_budget_s=11, broker_video_budget_s=22)
    http = BrokerHttp.from_settings(settings)
    assert http.base_url == "https://b.example"
    assert (http.project_key, http.poll_budget_s, http.media_budget_s) == ("k", 11, 22)
    client = http.client
    assert http.client is client
    await http.aclose()
    assert http.client is not client
    await http.aclose()
