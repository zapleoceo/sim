"""LlmPort over the Broker job API: `POST /v1/jobs?capability=...` with a pinned model, then the
poll. The catalog half of the port (`list_models`) is delegated to `BrokerModelCatalog`."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sim.adapters.broker.blocks import (
    clamp_max_tokens,
    message_payload,
    pick_capability,
    response_format,
    workflow_tag,
)
from sim.adapters.broker.http import BrokerHttp, is_subscription_model, usage_of
from sim.adapters.broker.models import BrokerModelCatalog
from sim.ports import ChatMessage, ChatResult, ModelInfo, PortError

JOBS_PATH = "/v1/jobs"
# Poll-body fields too large or useless to keep in `ChatResult.raw` (it is logged to llm_call).
BULKY_KEYS = frozenset({"files", "video_base64", "audio_base64", "image_base64"})


def chat_body(*, model: str, messages: Sequence[ChatMessage], json_schema: dict[str, Any] | None,
              max_tokens: int, workflow: str | None, label: str) -> dict[str, Any]:
    """The `/v1/jobs` body. Subscription models (claude-sub/, codex-sub/) reject anything but the
    default max_tokens / temperature, so neither is sent for them."""
    body: dict[str, Any] = {"messages": [message_payload(message) for message in messages]}
    if model:
        body["model"] = model
    if json_schema is not None:
        body["response_format"] = response_format(json_schema, label)
    if not is_subscription_model(model):
        body["max_tokens"] = clamp_max_tokens(max_tokens)
    if workflow:
        body["workflow"] = workflow
    return body


class BrokerLlm:
    def __init__(self, http: BrokerHttp, *, catalog: BrokerModelCatalog | None = None) -> None:
        self.http = http
        self.catalog = catalog or BrokerModelCatalog(http)

    async def chat(self, *, model: str, messages: Sequence[ChatMessage],
                   json_schema: dict[str, Any] | None = None, max_tokens: int = 4096,
                   label: str = "") -> ChatResult:
        if not messages:
            raise PortError("chat: no messages")
        capability = pick_capability(model, messages, json_schema)
        workflow = workflow_tag(label)
        body = chat_body(model=model, messages=messages, json_schema=json_schema,
                         max_tokens=max_tokens, workflow=workflow, label=label)
        params = {"capability": capability}
        if workflow:
            params["workflow"] = workflow
        budget = (self.http.media_budget_s if is_subscription_model(model)
                  else self.http.poll_budget_s)
        outcome = await self.http.submit(JOBS_PATH, body, what=f"chat {capability}",
                                         budget_s=budget, params=params)
        text = outcome.data.get("text")
        return ChatResult(
            text=text if isinstance(text, str) else "",
            usage=usage_of(outcome.data, model=model, latency_ms=outcome.latency_ms,
                           job_id=outcome.job_id),
            raw={k: v for k, v in outcome.data.items() if k not in BULKY_KEYS},
        )

    async def list_models(self) -> list[ModelInfo]:
        return await self.catalog.list_models()
