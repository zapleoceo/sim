"""Idempotency-Key of the Broker's queued jobs (docs/broker.md §2b): a retried task re-attaches
to the job it already paid for instead of submitting a new one."""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from sim.adapters.broker.chat import BrokerLlm
from sim.adapters.broker.http import MAX_KEY_HOPS, BrokerHttp, BrokerHttpError
from sim.adapters.broker.idempotency import HEADER, conflict_job_id, job_key, next_key
from sim.adapters.broker.image import BrokerImageGen
from sim.adapters.broker.video import BrokerVideoGen
from sim.call_scope import CallScope, call_scope
from sim.ports import ChatMessage, ChatPart, PortError
from tests.adapters.helpers import done, failed, pending, submitted

KEY_RULE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def user(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", parts=[ChatPart(text=text)])]


def key_of(route: respx.Route, index: int = -1) -> str | None:
    return route.calls[index].request.headers.get(HEADER)


def replayed(job_id: int, status: str, **fields: object) -> httpx.Response:
    return httpx.Response(200, headers={"Idempotent-Replay": "true"},
                          json={"job_id": job_id, "status": status,
                                "poll_url": f"/v1/jobs/{job_id}", **fields})


def conflict(existing: int | None) -> httpx.Response:
    detail: dict[str, object] = {"error": "idempotency_key_conflict",
                                 "message": "Idempotency-Key was already used"}
    if existing is not None:
        detail["existing_job_id"] = existing
    return httpx.Response(409, json={"detail": detail})


async def test_same_request_twice_sends_the_same_key(http: BrokerHttp,
                                                     broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="hi"))
    llm = BrokerLlm(http)
    for _ in range(2):                          # two attempts of the same task
        with call_scope("job-42"):
            await llm.chat(model="m", messages=user("привет"), label="reply")
    first, second = key_of(post, 0), key_of(post, 1)
    assert first is not None and first == second
    assert first.startswith("sim-job-") and KEY_RULE.fullmatch(first)


async def test_another_request_or_task_gets_another_key(http: BrokerHttp,
                                                        broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="hi"))
    llm = BrokerLlm(http)
    with call_scope("job-42"):
        await llm.chat(model="m", messages=user("привет"), label="reply")
        await llm.chat(model="m", messages=user("привет, исправь план"), label="reply")
    with call_scope("job-43"):                  # a deliberate re-run is a new job
        await llm.chat(model="m", messages=user("привет"), label="reply")
    keys = [key_of(post, index) for index in range(3)]
    assert None not in keys and len(set(keys)) == 3


async def test_no_scope_sends_no_key(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="hi"))
    await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert HEADER not in post.calls.last.request.headers


async def test_image_and_video_jobs_carry_the_key(http: BrokerHttp,
                                                  broker: respx.MockRouter) -> None:
    images = broker.post("/v1/images/jobs").mock(return_value=submitted(11))
    broker.get("/v1/jobs/11").mock(return_value=done(11, image_base64="aW1n",
                                                      mime_type="image/png"))
    videos = broker.post("/v1/video/jobs").mock(return_value=submitted(21))
    broker.get("/v1/jobs/21").mock(return_value=done(21, video_base64="Y2xpcA=="))
    for _ in range(2):
        with call_scope("job-5"):
            await BrokerImageGen(http).generate_image(prompt="selfie", references=[],
                                                      model="gemini/img")
            await BrokerVideoGen(http).generate_video(
                prompt="she waves", first_frame=(b"\xff\xd8\xff", "image/jpeg"), model="v")
    assert key_of(images, 0) is not None and key_of(images, 0) == key_of(images, 1)
    assert key_of(videos, 0) is not None and key_of(videos, 0) == key_of(videos, 1)
    assert key_of(images, 0) != key_of(videos, 0)


async def test_codex_image_route_carries_the_key(http: BrokerHttp,
                                                 broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted(12))
    broker.get("/v1/jobs/12").mock(return_value=done(
        12, files=[{"path": "out.png", "content_b64": "aW1n"}]))
    with call_scope("job-6"):
        await BrokerImageGen(http).generate_image(prompt="selfie", references=[],
                                                  model="codex-sub/gpt-5")
    assert key_of(post) is not None


async def test_replayed_running_job_is_polled_not_paid_again(
        http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=replayed(7, "running"))
    poll = broker.get("/v1/jobs/7").mock(side_effect=[pending(), done(text="the first one")])
    with call_scope("job-42"):
        result = await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert result.text == "the first one"
    assert post.call_count == 1 and poll.call_count == 2


async def test_sequential_scope_keys_by_position_not_text(http: BrokerHttp,
                                                          broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="plan"))
    llm = BrokerLlm(http)
    with call_scope("job-42"), call_scope("reply-3-41", sequential=True):
        await llm.chat(model="m", messages=user("Сейчас 14:05"), label="reply")
        await llm.chat(model="m", messages=user("Сейчас 14:05, исправь"), label="reply")
    with call_scope("job-42"), call_scope("reply-3-41", sequential=True):
        await llm.chat(model="m", messages=user("Сейчас 14:10"), label="reply")
    first, second, retried = (key_of(post, index) for index in range(3))
    assert first is not None and first != second
    assert retried == first                     # the clock moved, the call is the same


async def test_conflict_reattaches_to_the_existing_job(http: BrokerHttp,
                                                       broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=conflict(existing=99))
    existing = broker.get("/v1/jobs/99").mock(side_effect=[
        httpx.Response(200, json={"job_id": 99, "status": "running"}),
        done(99, text="the plan of the first attempt"),
    ])
    with call_scope("reply-3-41", sequential=True):
        result = await BrokerLlm(http).chat(model="m", messages=user("Сейчас 14:10"))
    assert result.text == "the plan of the first attempt"
    assert result.usage.job_id == "99"
    assert post.call_count == 1 and existing.call_count == 2


async def test_conflict_without_a_job_stays_a_terminal_error(http: BrokerHttp,
                                                             broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=conflict(existing=None))
    with call_scope("job-1"), pytest.raises(BrokerHttpError) as info:
        await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert info.value.status == 409 and info.value.retryable is False


async def test_bad_request_keeps_its_terminal_error(http: BrokerHttp,
                                                    broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=httpx.Response(422, json={"detail": "bad"}))
    with call_scope("job-1"), pytest.raises(BrokerHttpError) as info:
        await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert info.value.status == 422 and info.value.retryable is False


async def test_replayed_failure_walks_on_to_the_next_key(http: BrokerHttp,
                                                         broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(side_effect=[
        replayed(7, "error", error="stale", error_code="worker_restarted"), submitted(8)])
    broker.get("/v1/jobs/8").mock(return_value=done(8, text="fresh"))
    with call_scope("job-42"):
        result = await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert result.text == "fresh"
    first, second = key_of(post, 0), key_of(post, 1)
    assert first is not None and second == next_key(first, "7")


async def test_conflict_with_a_failed_job_walks_on(http: BrokerHttp,
                                                   broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(side_effect=[conflict(existing=7), submitted(8)])
    broker.get("/v1/jobs/7").mock(return_value=failed(7, error="cancelled by owner"))
    broker.get("/v1/jobs/8").mock(return_value=done(8, text="fresh"))
    with call_scope("reply-3-41", sequential=True):
        result = await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert result.text == "fresh"
    assert key_of(post, 1) == next_key(str(key_of(post, 0)), "7")


async def test_endless_failed_replays_end_with_the_job_error(
        http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=replayed(7, "error", error="refused"))
    with call_scope("job-42"), pytest.raises(PortError, match="refused") as info:
        await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert post.call_count == MAX_KEY_HOPS + 1
    assert info.value.retryable is False


async def test_fresh_job_failing_at_once_is_not_resubmitted(http: BrokerHttp,
                                                            broker: respx.MockRouter) -> None:
    """Only a replay walks on: a new key's own failure is the answer of this attempt."""
    post = broker.post("/v1/jobs").mock(return_value=httpx.Response(
        202, json={"job_id": 7, "status": "error", "error": "refused"}))
    with call_scope("job-42"), pytest.raises(PortError, match="refused"):
        await BrokerLlm(http).chat(model="m", messages=user("привет"))
    assert post.call_count == 1


def test_job_key_is_deterministic_and_valid() -> None:
    body = {"messages": [{"role": "user", "content": "hi"}], "workflow": "sim-reply"}
    key = job_key(CallScope("job-1"), "/v1/jobs", {"capability": "structured"}, body)
    assert key == job_key(CallScope("job-1"), "/v1/jobs", {"capability": "structured"},
                          dict(reversed(list(body.items()))))
    assert key != job_key(CallScope("job-1"), "/v1/jobs", {"capability": "chat:smart"}, body)
    assert key != job_key(CallScope("job-2"), "/v1/jobs", {"capability": "structured"}, body)
    assert key is not None and KEY_RULE.fullmatch(key) and len(key) == 72
    assert job_key(None, "/v1/jobs", None, body) is None


def test_sequential_key_counts_calls() -> None:
    scope = CallScope("reply-1-2", sequential=True)
    first = job_key(scope, "/v1/jobs", None, {"messages": ["a"], "model": "m"})
    second = job_key(scope, "/v1/jobs", None, {"messages": ["a"], "model": "m"})
    assert scope.calls == 2 and first != second
    other_model = job_key(CallScope("reply-1-2", sequential=True), "/v1/jobs", None,
                          {"messages": ["a"], "model": "other"})
    assert other_model != first


@pytest.mark.parametrize(("status", "payload", "expected"), [
    (409, {"detail": {"error": "idempotency_key_conflict", "existing_job_id": 5}}, "5"),
    (409, {"detail": {"error": "idempotency_key_conflict"}}, None),
    (409, {"detail": "conflict"}, None),
    (409, None, None),
    (400, {"detail": {"error": "idempotency_key_conflict", "existing_job_id": 5}}, None),
])
def test_conflict_job_id(status: int, payload: object, expected: str | None) -> None:
    assert conflict_job_id(status, payload) == expected
