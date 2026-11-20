"""Typed Broker ASR transport contracts, using only MockTransport."""
from __future__ import annotations

import httpx
import pytest

from sim.adapters.llm.preview_asr import CONTRACT, CONTRACT_HEADER, BrokerPreviewAsr
from sim.ports.llm import LLMError
from sim.ports.preview_asr import MAX_ASR_UPLOAD_BYTES, AsrReceipt

KEY = "preview-asr-1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3"
POLL_URL = "https://wrong.invalid/"
FRESH = {"job_id": 17, "poll_url": POLL_URL, "poll_after_s": 2}
DONE = {**FRESH, "status": "done", "text": "Пример", "error_code": None,
        "provider": None, "model": None, "model_served": None, "cost_usd": None, "latency_ms": None}


async def test_asr_lost_post_ack_only_recovers_by_key_then_reads_status():
    calls = []
    lookups = 0

    async def route(request):
        nonlocal lookups
        path = request.url.path
        calls.append((request.method, path))
        assert request.url.host == "broker.invalid"
        assert request.headers["X-Project-Key"] == "test-project"
        if request.method == "POST":
            assert request.url.params["workflow"] == "preview"
            assert request.headers["Idempotency-Key"] == KEY
            body = await request.aread()
            assert b'name="file"; filename="sample.ogg"' in body and b"synthetic audio" in body
            raise httpx.ReadTimeout("synthetic lost response", request=request)
        if "/by-key/" in path:
            if not path.endswith(KEY):
                return httpx.Response(404, headers={CONTRACT_HEADER: CONTRACT})
            lookups += 1
            if lookups == 1:
                return httpx.Response(404, headers={CONTRACT_HEADER: CONTRACT})
        return httpx.Response(200, json=DONE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        adapter = BrokerPreviewAsr("https://broker.invalid", "test-project", lambda: client)
        with pytest.raises(LLMError):
            await adapter.submit(KEY, b"synthetic audio", "sample.ogg")
        assert await adapter.by_key(KEY) is None
        receipt = await adapter.by_key(KEY)
        assert receipt == AsrReceipt("17", "done", "Пример")
        assert await adapter.status("17") == receipt
    assert calls[0][0] == "GET" and "/by-key/" in calls[0][1] and not calls[0][1].endswith(KEY)
    assert calls[1:] == [
        ("POST", "/v1/transcribe/keyed-jobs"),
        ("GET", f"/v1/transcribe/jobs/by-key/{KEY}"),
        ("GET", f"/v1/transcribe/jobs/by-key/{KEY}"),
        ("GET", "/v1/transcribe/jobs/17"),
    ]


@pytest.mark.parametrize(("code", "body", "expected"), [
    (202, FRESH, AsrReceipt("17", "pending")),
    (202, {**FRESH, "status": "pending"}, AsrReceipt("17", "pending")),
    (200, DONE, AsrReceipt("17", "done", "Пример")),
])
async def test_asr_fresh_and_replay_wire_shapes(code, body, expected):
    def route(request):
        if request.method == "GET":
            return httpx.Response(404, headers={CONTRACT_HEADER: CONTRACT})
        return httpx.Response(code, json=body, headers={CONTRACT_HEADER: CONTRACT})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        port = BrokerPreviewAsr("https://broker.invalid", "test-project", lambda: client)
        assert await port.submit(KEY, b"sample", "test.ogg") == expected


async def test_asr_plain_404_never_grants_first_post():
    methods = []

    def route(request):
        methods.append(request.method)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        adapter = BrokerPreviewAsr("https://broker.invalid", "test-project", lambda: client)
        with pytest.raises(LLMError, match="not verified"):
            await adapter.submit(KEY, b"sample", "test.ogg")
    assert methods == ["GET"]


@pytest.mark.parametrize(("status", "marker"), [(202, None), (200, "legacy"), (404, CONTRACT)])
async def test_asr_does_not_fallback_if_keyed_submission_is_unavailable(status, marker):
    calls = []

    def route(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404, headers={CONTRACT_HEADER: CONTRACT})
        return httpx.Response(status, json=DONE if status == 200 else FRESH,
                              headers={CONTRACT_HEADER: marker} if marker else {})

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        adapter = BrokerPreviewAsr("https://broker.invalid", "test-project", lambda: client)
        with pytest.raises(LLMError):
            await adapter.submit(KEY, b"sample", "test.ogg")
    assert len(calls) == 2
    assert calls[1] == ("POST", "/v1/transcribe/keyed-jobs")


@pytest.mark.parametrize("bad", [
    {**DONE, "job_id": True},
    {**DONE, "job_id": 18},
    {**DONE, "status": "whatever"},
    {**DONE, "text": None},
    {**DONE, "text": "é" * (128 * 1024 + 1)},
])
async def test_asr_invalid_receipt_does_not_retry(bad):
    calls = []

    def route(request):
        calls.append(request)
        return httpx.Response(200, json=bad)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route), follow_redirects=True) as client:
        adapter = BrokerPreviewAsr("https://broker.invalid", "test-project", lambda: client)
        with pytest.raises(LLMError):
            await adapter.status("17")
    assert len(calls) == 1 and calls[0].url.host == "broker.invalid"


async def test_asr_rejects_oversize_and_invalid_paths_before_request():
    def route(_request):
        pytest.fail("invalid input must not go to Broker")

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
        adapter = BrokerPreviewAsr("https://broker.invalid", "test-project", lambda: client)
        with pytest.raises(LLMError, match="size"):
            await adapter.submit(KEY, b"x" * (MAX_ASR_UPLOAD_BYTES + 1), "test.ogg")
        with pytest.raises(LLMError, match="identifier"):
            await adapter.status("../job")
        with pytest.raises(LLMError, match="filename"):
            await adapter.submit(KEY, b"data", "../test.ogg")
