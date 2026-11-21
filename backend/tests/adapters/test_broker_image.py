"""BrokerImageGen: /v1/images/jobs, and the Codex subscription fallback."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.image import MAX_CODEX_REFERENCES, BrokerImageGen, image_of
from sim.ports import PortError
from tests.adapters.helpers import FakeTime, done, failed, pending, sent_json, submitted

PNG = b"\x89PNG\r\n\x1a\nfake"
JPEG = b"\xff\xd8\xff fake"
REFS = [(b"face-1", "image/jpeg"), (b"face-2", "image/png")]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_images_job(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/images/jobs").mock(return_value=submitted(11))
    broker.get("/v1/jobs/11").mock(return_value=done(
        11, image_base64=b64(JPEG), mime_type="image/jpeg", cost_usd=0.04,
        model="gemini/gemini-image"))
    result = await BrokerImageGen(http).generate_image(
        prompt="selfie by the window", references=REFS, model="gemini/gemini-image")
    body = sent_json(post)
    assert body == {"prompt": "selfie by the window",
                    "references": [{"image_base64": b64(b"face-1"), "mime": "image/jpeg"},
                                   {"image_base64": b64(b"face-2"), "mime": "image/png"}],
                    "aspect_ratio": "3:4", "workflow": "sim-photo",
                    "model": "gemini/gemini-image"}
    assert (result.data, result.mime) == (JPEG, "image/jpeg")
    assert result.usage.cost_usd == pytest.approx(0.04)
    assert result.usage.job_id == "11"


async def test_images_job_uses_media_budget(http: BrokerHttp, broker: respx.MockRouter,
                                            fake_time: FakeTime) -> None:
    broker.post("/v1/images/jobs").mock(return_value=submitted(11))
    broker.get("/v1/jobs/11").mock(return_value=pending(11, poll_after_s=15))
    with pytest.raises(PortError, match="still pending after 600s") as info:
        await BrokerImageGen(http).generate_image(prompt="p", references=[], model="")
    assert info.value.retryable is True
    assert fake_time.now - 1000.0 >= 600


async def test_codex_fallback(http: BrokerHttp, broker: respx.MockRouter) -> None:
    images = broker.post("/v1/images/jobs")
    post = broker.post("/v1/jobs").mock(return_value=submitted(12))
    broker.get("/v1/jobs/12").mock(return_value=done(12, text="done", files=[
        {"path": "notes.txt", "content_b64": b64(b"x")},
        {"path": "out/skipped.png", "skipped": "too_large"},
        {"path": "out/selfie.PNG", "content_b64": b64(PNG)},
    ]))
    refs = [(f"ref-{n}".encode(), "image/jpeg") for n in range(7)]
    result = await BrokerImageGen(http).generate_image(prompt="selfie", references=refs,
                                                       model="codex-sub/gpt-6.2")
    assert images.call_count == 0
    request = post.calls.last.request
    assert request.url.params["capability"] == "chat:fast"
    body = sent_json(post)
    assert body["model"] == "codex-sub/gpt-6.2"
    assert "max_tokens" not in body
    assert "no_shell" not in body
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"].startswith("$imagegen")
    assert "selfie" in content[0]["text"]
    assert len(content) == 1 + MAX_CODEX_REFERENCES
    assert content[1] == {"type": "image_url",
                          "image_url": {"url": f"data:image/jpeg;base64,{b64(b'ref-0')}"}}
    assert (result.data, result.mime) == (PNG, "image/png")


async def test_job_without_picture(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/images/jobs").mock(return_value=submitted(11))
    broker.get("/v1/jobs/11").mock(return_value=done(11, text="I cannot draw that"))
    with pytest.raises(PortError, match="without a picture") as info:
        await BrokerImageGen(http).generate_image(prompt="p", references=REFS, model="")
    assert info.value.retryable is False


async def test_blocked_prompt(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/images/jobs").mock(return_value=submitted(11))
    broker.get("/v1/jobs/11").mock(return_value=failed(11, error="prompt blocked"))
    with pytest.raises(PortError, match="prompt blocked") as info:
        await BrokerImageGen(http).generate_image(prompt="p", references=REFS, model="")
    assert info.value.retryable is False


async def test_empty_prompt(http: BrokerHttp) -> None:
    with pytest.raises(PortError, match="empty prompt"):
        await BrokerImageGen(http).generate_image(prompt=" ", references=REFS, model="")


async def test_submit_refused(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/images/jobs").mock(return_value=httpx.Response(400, text="bad image"))
    with pytest.raises(PortError, match="HTTP 400") as info:
        await BrokerImageGen(http).generate_image(prompt="p", references=REFS, model="")
    assert info.value.retryable is False


def test_image_of_variants() -> None:
    assert image_of({"image_base64": b64(JPEG)}) == (JPEG, "image/png")
    assert image_of({"images": [{"data": b64(JPEG), "mime": "image/jpeg"}]}) == (
        JPEG, "image/jpeg")
    assert image_of({"images": [{"b64": b64(PNG)}]}) == (PNG, "image/png")
    assert image_of({"files": [{"path": "a.png", "content_b64": b64(PNG)}]}) == (
        PNG, "image/png")
    with pytest.raises(PortError):
        image_of({"images": [], "files": "nope"})
