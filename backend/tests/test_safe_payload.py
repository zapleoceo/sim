import json

import pytest
import respx

from sim.ports.llm import LLMError
from sim.services.safe_payload import LIMIT, exchange_out, safe_payload
from tests.conftest import OPERATOR, OWNER, login
from tests.test_broker import DONE, MSGS, URL, make


def test_nested_credentials_embedded_json_and_private_keys_are_removed():
    raw = {"Authorization": "short-auth", "nested": [{"api_key": "short-key", "cookie": "sid=private"}],
           "text": 'Bearer short.bearer password=hidden -----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----',
           "json": '{"refresh_token":"short-refresh", "reply":"hello"}',
           "url": "https://user:private-pass@host/?token=private-query",
           "headers_text": "Cookie: first=private-cookie; second=private-cookie-2"}
    result = json.dumps(safe_payload(raw))
    for value in ("short-auth", "short-key", "sid=private", "short.bearer", "hidden", "private-material", "short-refresh",
                  "private-pass", "private-query", "private-cookie"):
        assert value not in result
    assert "hello" in result


def test_bounded_payload_and_read_projection_preserve_truncation_without_reconstructing():
    payload = safe_payload({"text": "z" * (LIMIT + 10)})
    assert payload["truncated"] and len(json.dumps(payload)) < LIMIT + 200
    assert exchange_out(None) is None
    assert exchange_out({"request": payload})["request"]["truncated"]
    assert "response" not in exchange_out({"request": payload})
    assert "synthetic-project-secret" not in json.dumps(safe_payload(
        {"synthetic-project-secret": "echo"}, secrets=("synthetic-project-secret",)))


@respx.mock
async def test_broker_captures_actual_subscription_body_and_response_without_headers():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 1})
    respx.get(f"{URL}/v1/jobs/1").respond(200, json={**DONE, "credential": "hidden", "text": "key-1"})
    result = await make().chat(MSGS, model="codex-sub/gpt-6.1-sol", max_tokens=123)
    request = result.exchange["request"]["content"]
    assert request["body"]["messages"] == MSGS
    assert "max_tokens" not in request["body"]
    assert "headers" not in request
    assert "hidden" not in json.dumps(result.exchange) and "key-1" not in json.dumps(result.exchange)


@respx.mock
async def test_failed_broker_job_keeps_actual_error_response():
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 1})
    respx.get(f"{URL}/v1/jobs/1").respond(200, json={"status": "error", "error": "offline", "token": "hidden"})
    with pytest.raises(LLMError) as caught:
        await make().chat(MSGS)
    assert caught.value.exchange["response"]["content"]["status"] == "error"
    assert "hidden" not in json.dumps(caught.value.exchange)


async def test_owner_trace_redacts_legacy_payload_and_does_not_bulk_list_it(api, container):
    from sim.services.tracing import Tracer

    trace_id = "a" * 32
    await Tracer(container.factory).record("llm", "synthetic", trace_id=trace_id,
        error="Cookie: first=private-value; second=private-value",
        detail={"broker_exchange": {"request": {"content": {"token": "private-value", "messages": MSGS}, "truncated": False}}})
    await login(api, *OWNER)
    result = await api.get(f"/api/activity/traces/{trace_id}")
    assert result.status_code == 200 and "private-value" not in result.text
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json()["steps"][0]["broker_exchange"]["request"]["content"]["messages"] == MSGS
    listing = await api.get("/api/activity/log")
    assert "broker_exchange" not in listing.text
    await login(api, *OPERATOR)
    assert (await api.get(f"/api/activity/traces/{trace_id}")).status_code == 403
