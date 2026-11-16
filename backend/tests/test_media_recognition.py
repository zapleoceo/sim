"""Offline subscription contract fixtures; no connectors or provider calls."""
from unittest.mock import AsyncMock

import pytest

from sim.ports.llm import LLMResult
from sim.services.safe_payload import safe_payload
from sim.services.media_recognition import (
    DEFAULT_MODEL,
    MAX_FILE_BYTES,
    SubscriptionMediaRecognizer,
    UnsupportedMedia,
    prepare,
)


def client():
    llm = AsyncMock()
    llm.list_models.return_value = [{
        "id": DEFAULT_MODEL, "billing_type": "subscription", "available": True,
        "capabilities": ["chat:fast"],
        "job_request": {"message_content": ["text", "image_data_url"]},
    }]
    llm.chat.return_value = LLMResult("Содержимое документа")
    return llm


async def test_document_uses_exact_subscription_without_paid_transcription():
    llm = client()
    assert await SubscriptionMediaRecognizer(llm).recognize(b"hello", "document")
    args, kw = llm.chat.call_args
    assert kw == {"capability": "chat:fast", "model": DEFAULT_MODEL,
                  "workflow": "incoming_media"}
    assert args[0][1]["content"] == [{"type": "text", "text": "hello"}]
    llm.transcribe.assert_not_called()


@pytest.mark.parametrize("data,kind", [
    (b"", "document"), (b"a" * (MAX_FILE_BYTES + 1), "image"),
    (b"OggS" + b"\0" * 30, "voice"), (b"%PDF-1.7", "document"),
    (b"\xff\xfe", "document"), (b"hello\0", "document"),
    (b"<svg></svg>", "image"), (b"not a video", "video"),
])
async def test_unsupported_never_calls_broker(data, kind):
    llm = client()
    with pytest.raises(UnsupportedMedia):
        await SubscriptionMediaRecognizer(llm).recognize(data, kind)
    llm.chat.assert_not_called()
    llm.transcribe.assert_not_called()


async def test_catalog_unavailable_never_falls_back():
    llm = client()
    llm.list_models.return_value[0]["available"] = False
    with pytest.raises(UnsupportedMedia, match="unavailable"):
        await SubscriptionMediaRecognizer(llm).recognize(b"hello", "document")
    llm.chat.assert_not_called()


def test_plain_text_is_data_not_executed():
    assert prepare(b"import os; os.remove('x')", "document").mime == "text/plain"
    with pytest.raises(ValueError, match="subscription"):
        SubscriptionMediaRecognizer(client(), "gemini/gemini-3.5-flash")


def test_binary_data_urls_never_reach_diagnostics():
    payload = {"messages": [{"content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64,cHJpdmF0ZQ==",
    }}]}]}
    result = safe_payload(payload)
    assert "cHJpdmF0ZQ" not in str(result)
    assert "[REDACTED MEDIA]" in str(result)
