from __future__ import annotations

import json

import httpx
import pytest
import respx

from sim.adapters.llm.broker import BrokerLLM, is_subscription_model
from sim.ports.llm import LLMError

URL = "https://broker.test"
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]


async def no_sleep(_: float) -> None:
    return None


def make(**kw) -> BrokerLLM:
    return BrokerLLM(URL, "key-1", sleep=no_sleep, **kw)


DONE = {
    "job_id": 7, "status": "done", "text": "{\"reply\": \"x\"}", "provider": "anthropic",
    "model": "claude-x", "tokens_in": 120, "tokens_out": 30, "cache_read_tokens": 100,
    "cost_usd": 0.0123,
}


@respx.mock
async def test_submit_and_poll_maps_result():
    submit = respx.post(f"{URL}/v1/jobs").respond(
        202, json={"job_id": 7, "status": "pending", "poll_url": "/v1/jobs/7", "poll_after_s": 1}
    )
    respx.get(f"{URL}/v1/jobs/7").mock(
        side_effect=[
            httpx.Response(200, json={"job_id": 7, "status": "pending", "poll_after_s": 1}),
            httpx.Response(200, json=DONE),
        ]
    )
    res = await make().chat(MSGS, capability="chat:sales", json_mode=True, workflow="reply",
                            max_tokens=500, temperature=0.4)
    assert res.text == DONE["text"]
    assert (res.model, res.provider) == ("claude-x", "anthropic")
    assert (res.tokens_in, res.tokens_out, res.cached_tokens) == (120, 30, 100)
    assert res.cost_usd == pytest.approx(0.0123) and res.job_id == "7"

    req = submit.calls.last.request
    assert req.url.params["capability"] == "chat:sales"
    assert req.headers["X-Project-Key"] == "key-1"
    body = json.loads(req.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 500 and body["temperature"] == 0.4
    assert body["workflow"] == "reply" and "model" not in body


@pytest.mark.parametrize("model", ["claude-sub/claude-sonnet-5-5", "codex-sub/gpt-6.1-sol"])
@respx.mock
async def test_subscription_model_omits_max_tokens_and_temperature(model):
    submit = respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 1, "poll_after_s": 1})
    respx.get(f"{URL}/v1/jobs/1").respond(200, json={**DONE, "job_id": 1})
    await make().chat(MSGS, model=model, max_tokens=500, temperature=0.4)
    body = json.loads(submit.calls.last.request.content)
    assert body["model"] == model
    assert "max_tokens" not in body and "temperature" not in body
    assert is_subscription_model(model)


@respx.mock
async def test_result_meta_fallback():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 2})
    respx.get(f"{URL}/v1/jobs/2").respond(
        200, json={"status": "done", "text": "t",
                   "result_meta": {"model": "m", "provider": "p", "tokens_in": 5, "cost_usd": 0}}
    )
    res = await make().chat(MSGS)
    assert (res.model, res.provider, res.tokens_in, res.cost_usd) == ("m", "p", 5, 0.0)


@respx.mock
async def test_job_error_raises_llm_error():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 3})
    respx.get(f"{URL}/v1/jobs/3").respond(200, json={"status": "error", "error": "all providers down"})
    with pytest.raises(LLMError, match="all providers down"):
        await make().chat(MSGS)


@respx.mock
async def test_submit_http_error_raises_llm_error():
    respx.post(f"{URL}/v1/jobs").respond(403, json={"detail": "no scope"})
    with pytest.raises(LLMError, match="403"):
        await make().chat(MSGS)


@respx.mock
async def test_transient_poll_errors_are_tolerated():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 4})
    respx.get(f"{URL}/v1/jobs/4").mock(
        side_effect=[httpx.Response(502), httpx.Response(502), httpx.Response(200, json=DONE)]
    )
    assert (await make().chat(MSGS)).text == DONE["text"]


@respx.mock
async def test_too_many_poll_errors_gives_up():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 5})
    respx.get(f"{URL}/v1/jobs/5").respond(502)
    with pytest.raises(LLMError, match="poll errors"):
        await make().chat(MSGS)


@respx.mock
async def test_budget_exhausted():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 6})
    respx.get(f"{URL}/v1/jobs/6").respond(200, json={"status": "pending", "poll_after_s": 1})
    with pytest.raises(LLMError, match="budget"):
        await make(poll_budget_s=0).chat(MSGS)


def test_budget_selection():
    llm = make(poll_budget_s=10, subscription_budget_s=99)
    assert llm.budget_for("claude-sub/x") == 99 and llm.budget_for(None) == 10


@respx.mock
async def test_transcribe_job_flow():
    respx.post(f"{URL}/v1/transcribe/jobs").respond(202, json={"job_id": 9})
    respx.get(f"{URL}/v1/jobs/9").respond(
        200, json={"status": "done", "text": "привет", "result_meta": {"model": "whisper"}}
    )
    res = await make().transcribe(b"audio", "voice.ogg")
    assert res.text == "привет" and res.model == "whisper"


@respx.mock
async def test_list_models():
    respx.get(f"{URL}/v1/models").respond(200, json={"object": "list", "data": [{"id": "a"}, {"id": "b"}]})
    assert [m["id"] for m in await make().list_models()] == ["a", "b"]


@respx.mock
async def test_default_max_tokens_is_sent_for_regular_models():
    submit = respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 1, "poll_after_s": 1})
    respx.get(f"{URL}/v1/jobs/1").respond(200, json={**DONE, "job_id": 1})
    await make().chat(MSGS, model="gemini/flash")
    await make().chat(MSGS)  # no pinned model: broker chain
    for call in submit.calls:
        assert json.loads(call.request.content)["max_tokens"] == 2048


@respx.mock
async def test_finish_reason_is_read_from_body_meta_or_choices():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 1, "poll_after_s": 1})
    poll = respx.get(f"{URL}/v1/jobs/1")
    poll.respond(200, json={**DONE, "finish_reason": "length"})
    assert (await make().chat(MSGS)).finish_reason == "length"
    poll.respond(200, json={**DONE, "result_meta": {"finish_reason": "stop"}})
    assert (await make().chat(MSGS)).finish_reason == "stop"
    poll.respond(200, json={**DONE, "choices": [{"finish_reason": "length"}]})
    assert (await make().chat(MSGS)).finish_reason == "length"
    poll.respond(200, json=DONE)
    assert (await make().chat(MSGS)).finish_reason is None
