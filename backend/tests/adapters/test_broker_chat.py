"""BrokerLlm.chat: request body per model kind, capability, result and usage."""
from __future__ import annotations

import httpx
import pytest
import respx

from sim.adapters.broker.chat import BrokerLlm
from sim.adapters.broker.http import BrokerHttp
from sim.ports import ChatMessage, ChatPart, PortError
from tests.adapters.helpers import done, pending, sent_json, submitted

SCHEMA = {"type": "object", "properties": {"messages": {"type": "array"}}}
MESSAGES = [ChatMessage(role="system", parts=[ChatPart(text="You are Marina.")]),
            ChatMessage(role="user", parts=[ChatPart(text="hi")])]


async def test_api_model_with_schema(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(
        text='{"messages": []}', model="gemini/gemini-3.5-flash", tokens_in=120,
        tokens_out=8, cost_usd=0.0004, files=[{"path": "x"}], finish_reason="stop"))
    result = await BrokerLlm(http).chat(model="gemini/gemini-3.5-flash", messages=MESSAGES,
                                        json_schema=SCHEMA, max_tokens=50_000, label="reply")
    body = sent_json(post)
    assert post.calls.last.request.url.params["capability"] == "structured"
    assert post.calls.last.request.url.params["workflow"] == "sim-reply"
    assert body["model"] == "gemini/gemini-3.5-flash"
    assert body["max_tokens"] == 16_384
    assert body["workflow"] == "sim-reply"
    assert "temperature" not in body
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["messages"] == [{"role": "system", "content": "You are Marina."},
                                {"role": "user", "content": "hi"}]
    assert result.text == '{"messages": []}'
    assert result.usage.model == "gemini/gemini-3.5-flash"
    assert (result.usage.tokens_in, result.usage.tokens_out) == (120, 8)
    assert result.usage.cost_usd == pytest.approx(0.0004)
    assert result.usage.job_id == "7"
    assert result.usage.latency_ms == 2000
    assert "files" not in result.raw
    assert result.raw["finish_reason"] == "stop"


@pytest.mark.parametrize("model", ["claude-sub/claude-sonnet-5-5", "codex-sub/gpt-6.2"])
async def test_subscription_model_gets_no_limits(http: BrokerHttp, broker: respx.MockRouter,
                                                 model: str) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="ok"))
    await BrokerLlm(http).chat(model=model, messages=MESSAGES, json_schema=SCHEMA)
    body = sent_json(post)
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert body["model"] == model
    assert "response_format" in body
    assert post.calls.last.request.url.params["capability"] == "chat:smart"
    assert "workflow" not in post.calls.last.request.url.params


async def test_subscription_model_uses_media_budget(http: BrokerHttp,
                                                    broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    answers = [*(pending(poll_after_s=15) for _ in range(8)), done(text="late")]
    poll = broker.get("/v1/jobs/7").mock(side_effect=answers)
    result = await BrokerLlm(http).chat(model="claude-sub/x", messages=MESSAGES)
    assert result.text == "late"           # 2 + 8 * 15 s is past the 60 s chat budget
    assert poll.call_count == 9


async def test_attachments_go_to_vision(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="a cat"))
    messages = [ChatMessage(role="user", parts=[ChatPart(text="describe"),
                                                ChatPart(data=b"jpg", mime="image/jpeg")])]
    result = await BrokerLlm(http).chat(model="gemini/flash", messages=messages)
    assert post.calls.last.request.url.params["capability"] == "vision"
    content = sent_json(post)["messages"][0]["content"]
    assert content[1] == {"type": "image_url",
                          "image_url": {"url": "data:image/jpeg;base64,anBn"}}
    assert "response_format" not in sent_json(post)
    assert result.text == "a cat"


async def test_chat_without_pin(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text=None))
    result = await BrokerLlm(http).chat(model="", messages=MESSAGES, max_tokens=300)
    assert "model" not in sent_json(post)
    assert sent_json(post)["max_tokens"] == 300
    assert result.text == ""
    assert result.usage.model == ""


async def test_chat_timeout_is_retryable(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=pending(poll_after_s=10))
    with pytest.raises(PortError, match="still pending") as info:
        await BrokerLlm(http).chat(model="gemini/flash", messages=MESSAGES)
    assert info.value.retryable is True


async def test_chat_job_error(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=httpx.Response(
        200, json={"job_id": 7, "status": "error", "error": "model_unavailable"}))
    with pytest.raises(PortError, match="chat structured job 7 error") as info:
        await BrokerLlm(http).chat(model="gemini/flash", messages=MESSAGES, json_schema=SCHEMA)
    assert info.value.retryable is False


async def test_chat_needs_messages(http: BrokerHttp) -> None:
    with pytest.raises(PortError, match="no messages"):
        await BrokerLlm(http).chat(model="gemini/flash", messages=[])
