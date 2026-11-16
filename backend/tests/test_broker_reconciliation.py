"""Offline Broker restarts, unknown POST outcomes and encrypted generation fences."""
import json
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from sim.adapters.db.models import BrokerRequest, Dialog, Message, Persona
from sim.adapters.db.session import transaction
from sim.adapters.llm.broker import BrokerLLM, BrokerTerminalError
from sim.domain.enums import Stage
from sim.ports.llm import LLMError
from sim.services.broker_requests import BrokerRequests
from sim.services.conversation import recovery_context
from sim.services.job_execution import Execution, ExecutionLost
from sim.services.prompt import DialogContext
from tests.conftest import decision_json, guard_json, inbound
from tests.test_ingest import ingest

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


@pytest.mark.parametrize("code", [400, 401, 402, 403, 422])
async def test_http_status_alone_does_not_authorize_resubmission(container, monkeypatch, code):
    posts = []

    def transport(request):
        if request.method == "POST":
            posts.append(request.headers["Idempotency-Key"])
            return httpx.Response(code)
        return httpx.Response(200, json={"pagination_complete": True, "next_cursor": None, "jobs": []})

    old, _, _ = adapter(container, monkeypatch, 1, transport)
    with pytest.raises(LLMError, match=f"HTTP {code}"):
        await old.chat(MESSAGES)
    await old.aclose()
    new, _, _ = adapter(container, monkeypatch, 2, transport)
    with pytest.raises(LLMError, match="resubmission blocked"):
        await new.chat(MESSAGES)
    assert len(posts) == 1
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


@pytest.mark.parametrize("change", ["clock", "profile_clock", "inbound", "settings"])
@pytest.mark.parametrize("timezone", ["Europe/Berlin", "Asia/Ho_Chi_Minh"])
async def test_real_conversation_recovery_reuses_generation_but_checks_fresh_time(
    container, world, monkeypatch, change, timezone,
):
    now = [datetime(2026, 11, 16, 23, 59, 30)]
    container.clock = lambda: now[0]
    question = "Посмотри мой профиль" if change == "profile_clock" else "Synthetic question"
    await ingest(container, world.channel.id, inbound(question, ext="recovery-in-1", at=now[0]))
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.timezone = timezone
    posts, jobs = [], {}
    ready = [False]

    def transport(request):
        if request.method == "POST":
            body = json.loads(request.content)
            posts.append(body)
            remote_id = str(100 + len(posts))
            jobs[remote_id] = body["workflow"]
            return httpx.Response(200, json={"job_id": remote_id})
        remote_id = request.url.path.rsplit("/", 1)[-1]
        workflow = jobs[remote_id]
        if not ready[0]:
            return httpx.Response(200, json={"status": "pending"})
        return httpx.Response(200, json={"status": "done",
            "model": "synthetic-guard" if workflow == "guard" else "synthetic-generator",
            "text": guard_json() if workflow == "guard" else decision_json(reply="Synthetic answer")})

    async def attempt(generation):
        broker, _, owner = adapter(container, monkeypatch, generation, transport)
        def next_call():
            index = owner.broker_call_index
            owner.broker_call_index += 1
            return owner, index
        monkeypatch.setattr("sim.adapters.llm.broker.next_broker_call", next_call)
        container.llm = broker
        try:
            async with transaction(container.factory) as s:
                dialog = (await s.scalars(select(Dialog))).unique().one()
                return await container.conversation(s).run_reply(dialog.id)
        finally:
            await broker.aclose()

    with pytest.raises(LLMError, match="still pending"):
        await attempt(1)
    now[0] += timedelta(seconds=91)  # normal expired lease, UTC date boundary
    ready[0] = True
    if change == "inbound":
        await ingest(container, world.channel.id, inbound("New synthetic context", ext="recovery-in-2", at=now[0]))
    elif change == "settings":
        async with transaction(container.factory) as s:
            (await s.get(Persona, world.persona.id)).agent_prompt += "\nNew owner instruction."
    outcome = await attempt(2)
    assert outcome.status == "sent"  # queued only; no channel transport runs
    generator_posts = [body for body in posts if body["workflow"] == "reply"]
    assert len(generator_posts) == (1 if change in {"clock", "profile_clock"} else 2)
    guard_posts = [body for body in posts if body["workflow"] == "guard"]
    assert guard_posts and now[0].isoformat() + "Z" in json.dumps(guard_posts[-1])
    if change == "profile_clock":
        assert "2026-11-16T23:59:30Z" in json.dumps(generator_posts[0])
        assert "not_enabled" in json.dumps(generator_posts[0])
        assert now[0].isoformat() + "Z" in json.dumps(guard_posts[-1])
    assert "2000-01-01" not in json.dumps(posts)  # canonical identity is never sent
    async with container.factory() as s:
        messages = list(await s.scalars(select(Message).where(Message.delivery == "queued")))
        assert len(messages) == 1


def test_profile_identity_preserves_fields_status_photo_and_user_timestamps():
    snapshot = '{"status":"available","bio":"User text 2026-11-16T23:59:30Z","name":"A"}'
    ctx = DialogContext(stage=Stage.FIRST_CONTACT, status="active", client_name="A",
        channel_kind="telegram", ref_code="", now=datetime(2026, 11, 17),
        contact_profile="Observed at 2026-11-17T00:00:00Z " + snapshot,
        contact_profile_identity=snapshot, contact_photo="data:image/jpeg;base64,synthetic")
    stable = recovery_context(ctx)
    assert stable.contact_profile == snapshot and stable.contact_photo == ctx.contact_photo
    assert recovery_context(replace(ctx, contact_profile="A later observation " + snapshot)) == stable
    assert recovery_context(replace(ctx, contact_profile_identity=snapshot.replace('"A"', '"B"'))) != stable
    assert recovery_context(replace(ctx, contact_profile_identity=snapshot.replace("available", "unavailable"))) != stable
    assert recovery_context(replace(ctx, contact_photo="changed photo")) != stable
    # An unstructured caller has no trusted provenance field: preserve its complete text.
    assert recovery_context(replace(ctx, contact_profile_identity=None)).contact_profile == ctx.contact_profile
