"""Offline Broker restarts, unknown POST outcomes and encrypted generation fences."""
import json

import httpx
import pytest
from sqlalchemy import select

from sim.adapters.db.models import BrokerRequest
from sim.adapters.llm.broker import BrokerLLM, BrokerTerminalError
from sim.ports.llm import LLMError
from sim.services.broker_requests import BrokerRequests
from sim.services.job_execution import Execution, ExecutionLost

MESSAGES = [{"role": "user", "content": "synthetic private request"}]
DONE = {"status": "done", "text": "synthetic answer", "model": "test-model"}


def adapter(container, monkeypatch, generation, transport, *, index=0, key="test-project"):
    store = BrokerRequests(container.factory, container.box)
    owner = Execution(None, 800001, generation, None)
    monkeypatch.setattr("sim.adapters.llm.broker.next_broker_call", lambda: (owner, index))
    broker = BrokerLLM("https://broker.invalid", key, requests=store, poll_budget_s=-1)
    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return broker, store, owner


async def test_lost_post_response_recovers_identity_without_resubmission(container, monkeypatch):
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append((request.headers["Idempotency-Key"], request.content))
            raise httpx.ReadTimeout("synthetic response lost after acceptance", request=request)
        if request.url.path == "/v1/jobs":
            key = posts[0][0]
            assert request.url.params["label.sim_request"] == key
            return httpx.Response(200, json={"pagination_complete": True, "next_cursor": None,
                "jobs": [{"job_id": 17, "idempotency_key": key, "labels": {"sim_request": key}}]})
        return httpx.Response(200, json=DONE)

    first, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError):
        await first.chat(MESSAGES)
    await first.aclose()
    restarted, _, _ = adapter(container, monkeypatch, 2, transport)
    result = await restarted.chat(MESSAGES)
    assert result.text == DONE["text"] and result.job_id == "17"
    assert len(posts) == 1  # one POST, even when the accepted response was lost
    await restarted.aclose()
    async with container.factory() as s:
        row = await s.get(BrokerRequest, (800001, 0))
        assert row.status == "done" and row.remote_id == "17" and row.generation == 2
        assert "synthetic" not in row.request_cipher + row.response_cipher
        assert "test-project" not in row.request_cipher


async def test_accepted_pending_job_is_polled_not_resubmitted(container, monkeypatch):
    requests = []
    status = ["pending"]

    def transport(request):
        requests.append(request.method)
        return httpx.Response(200, json={"job_id": 18} if request.method == "POST"
            else {**DONE, "status": status[0]})

    first, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError, match="still pending"):
        await first.chat(MESSAGES)
    await first.aclose()
    status[0] = "done"
    restarted, _, _ = adapter(container, monkeypatch, 2, transport)
    assert (await restarted.chat(MESSAGES)).text == DONE["text"]
    assert requests == ["POST", "GET", "GET"]
    await restarted.aclose()


async def test_unknown_prior_call_blocks_different_model_and_payload(container, monkeypatch):
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={"job_id": 19})
        return httpx.Response(200, json={"status": "pending"})

    first, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError):
        await first.chat(MESSAGES, model="original")
    await first.aclose()
    # Even a different call position cannot skip unresolved earlier/later calls.
    restarted, _, _ = adapter(container, monkeypatch, 2, transport, index=5)
    with pytest.raises(LLMError, match="still pending"):
        await restarted.chat([{"role": "user", "content": "new context"}], model="fallback")
    assert len(posts) == 1 and posts[0]["model"] == "original"
    await restarted.aclose()


@pytest.mark.parametrize("status", ["error", "failed", "cancelled", "canceled"])
async def test_terminal_error_is_committed_and_not_polled_forever(container, monkeypatch, status):
    calls = []

    def transport(request):
        calls.append(request.method)
        return httpx.Response(200, json={"job_id": 20} if request.method == "POST"
            else {"status": status, "error": "synthetic failure"})

    broker, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(BrokerTerminalError):
        await broker.chat(MESSAGES)
    with pytest.raises(BrokerTerminalError):
        await broker.chat(MESSAGES)
    assert calls == ["POST", "GET"]
    async with container.factory() as s:
        assert (await s.get(BrokerRequest, (800001, 0))).status == "error"
    await broker.aclose()


async def test_changed_scope_never_replays_with_another_key(container, monkeypatch):
    def transport(request):
        raise httpx.ReadTimeout("synthetic unknown", request=request)

    broker, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError):
        await broker.chat(MESSAGES)
    await broker.aclose()
    new, _, _ = adapter(container, monkeypatch, 2, lambda request: pytest.fail("must not call"), key="other-project")
    with pytest.raises(LLMError, match="scope changed"):
        await new.chat(MESSAGES)
    await new.aclose()


async def test_missing_or_pruned_remote_receipt_never_replays_post(container, monkeypatch):
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append(request.content)
            raise httpx.ReadTimeout("synthetic unknown", request=request)
        return httpx.Response(200, json={"pagination_complete": True, "next_cursor": None, "jobs": []})

    old, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError):
        await old.chat(MESSAGES)
    await old.aclose()
    new, _, _ = adapter(container, monkeypatch, 2, transport)
    with pytest.raises(LLMError, match="resubmission blocked"):
        await new.chat(MESSAGES)
    assert len(posts) == 1
    await new.aclose()


async def test_late_checkpoint_cannot_overwrite_new_generation(container):
    store = BrokerRequests(container.factory, container.box)
    old = Execution(None, 800002, 1, None)
    new = Execution(None, 800002, 2, None)
    request = {"params": {"capability": "chat:sales"}, "body": {"messages": MESSAGES}}
    entry = await store.prepare(old, 0, "scope", request)
    [adopted] = await store.pending(new, "scope")
    with pytest.raises(ExecutionLost):
        await store.checkpoint(entry, remote_id="99")
    await store.checkpoint(adopted, remote_id="23")
    async with container.factory() as s:
        rows = list(await s.scalars(select(BrokerRequest)))
        assert len(rows) == 1 and rows[0].remote_id == "23" and rows[0].generation == 2


async def test_oversized_request_is_rejected_before_submission(container, monkeypatch):
    broker, _, _ = adapter(container, monkeypatch, 1, lambda request: pytest.fail("must not call"))
    with pytest.raises(LLMError, match="storage limit"):
        await broker.chat([{"role": "user", "content": "x" * 2_000_001}])
    await broker.aclose()


async def test_changed_context_is_submitted_only_after_previous_terminal_result(container, monkeypatch):
    actions = []
    ready = [False]

    def transport(request):
        actions.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"job_id": 30 + actions.count("POST")})
        return httpx.Response(200, json={**DONE, "status": "done" if ready[0] else "pending"})

    old, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError):
        await old.chat(MESSAGES)
    await old.aclose()
    ready[0] = True
    new, _, _ = adapter(container, monkeypatch, 2, transport)
    result = await new.chat([{"role": "user", "content": "changed synthetic context"}])
    assert result.job_id == "32"
    assert actions == ["POST", "GET", "GET", "POST", "GET"]
    await new.aclose()


async def test_terminal_checkpoint_cannot_be_regressed_by_concurrent_observer(container):
    store = BrokerRequests(container.factory, container.box)
    owner = Execution(None, 800003, 1, None)
    entry = await store.prepare(owner, 0, "scope", {"body": {}, "params": {}})
    await store.checkpoint(entry, remote_id="24", status="done", response=DONE)
    observed = await store.checkpoint(entry, remote_id="24")
    assert observed.status == "done" and observed.response == DONE


async def test_confirmed_admission_rejection_can_retry_after_recovery(container, monkeypatch):
    admitted = [False]
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append(request.headers["Idempotency-Key"])
            return httpx.Response(200, json={"job_id": 44}) if admitted[0] else httpx.Response(402)
        return httpx.Response(200, json=DONE)

    old, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError, match="HTTP 402"):
        await old.chat(MESSAGES)
    await old.aclose()
    admitted[0] = True  # synthetic Broker state only, no real budget modification
    new, _, _ = adapter(container, monkeypatch, 2, transport)
    assert (await new.chat(MESSAGES)).job_id == "44"
    assert len(posts) == 2 and posts[0] != posts[1]
    await new.aclose()


@pytest.mark.parametrize("case", ["partial", "cursor", "wrong_key", "wrong_label", "duplicate", "malformed"])
async def test_untrusted_lookup_never_authorizes_submission(container, monkeypatch, case):
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append(request.headers["Idempotency-Key"])
            raise httpx.ReadTimeout("synthetic unknown", request=request)
        key = posts[0]
        row = {"job_id": 52, "idempotency_key": key, "labels": {"sim_request": key}}
        listing = {"pagination_complete": True, "next_cursor": None, "jobs": [row]}
        if case == "partial":
            listing["pagination_complete"] = False
        elif case == "cursor":
            listing["next_cursor"] = "next-page"
        elif case == "wrong_key":
            row["idempotency_key"] = "another-key"
        elif case == "wrong_label":
            row["labels"] = {"sim_request": "another-key"}
        elif case == "duplicate":
            listing["jobs"] = [row, row]
        else:
            listing["jobs"] = [None]
        return httpx.Response(200, json=listing)

    old, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError):
        await old.chat(MESSAGES)
    await old.aclose()
    new, _, _ = adapter(container, monkeypatch, 2, transport)
    with pytest.raises(LLMError, match="resubmission blocked"):
        await new.chat(MESSAGES)
    assert len(posts) == 1
    await new.aclose()


async def test_concurrent_terminal_checkpoint_is_authoritative(container, monkeypatch):
    store = BrokerRequests(container.factory, container.box)

    async def transport(request):
        if request.method == "POST":
            return httpx.Response(200, json={"job_id": 61})
        async with container.factory() as s:
            entry = store.entry(await s.get(BrokerRequest, (800001, 0)))
        await store.checkpoint(entry, status="error", response={"status": "error", "error": "first terminal"})
        return httpx.Response(200, json=DONE)  # late contradictory observer must not override

    broker, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(BrokerTerminalError, match="first terminal"):
        await broker.chat(MESSAGES)
    await broker.aclose()


async def test_recovery_does_not_book_previous_usage_again(container, monkeypatch):
    posts = []
    usage = {**DONE, "cost_usd": 0.12, "tokens_in": 15, "tokens_out": 4}

    def transport(request):
        if request.method == "POST":
            posts.append(request.content)
            return httpx.Response(200, json={"job_id": 62})
        return httpx.Response(200, json=usage)

    old, _, _ = adapter(container, monkeypatch, 1, transport)
    assert (await old.chat(MESSAGES)).cost_usd == 0.12
    await old.aclose()
    new, _, _ = adapter(container, monkeypatch, 2, transport)
    result = await new.chat(MESSAGES)
    assert len(posts) == 1 and result.text == DONE["text"]
    assert result.cost_usd is None and result.tokens_in is None and result.tokens_out is None
    assert result.exchange["accepted"]["content"]["recovered_result"] is True
    assert result.exchange["response"]["content"]["cost_usd"] == 0.12
    await new.aclose()
