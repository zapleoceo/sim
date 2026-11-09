"""Owner studio uses fake Broker only; CI exercises auth and fail-closed routing."""
import pytest
from conftest import OPERATOR, OWNER, login

from sim.ports.llm import LLMError

MODEL = "claude-sub/claude-sonnet-5-5"


@pytest.fixture
async def studio(api, container, fake_llm):
    container.settings.studio_owner_telegram_id = OWNER[0]

    async def models():
        return [{"id": MODEL, "available": True, "tested": True,
                 "billing_type": "subscription"},
                {"id": "gemini/paid", "available": True, "tested": True}]

    fake_llm.list_models = models
    token = await login(api, OWNER[0])
    api.headers["X-CSRF-Token"] = token
    return api


async def test_auth_required(api):
    assert (await api.get("/api/studio")).status_code == 401


async def test_private_account_gate(api, container, fake_llm):
    token = await login(api, OWNER[0])
    status = (await api.get("/api/studio")).json()
    assert status["text_available"] is False
    response = await api.post("/api/studio/chat", headers={"X-CSRF-Token": token},
                              json={"model": MODEL, "messages": [{"role": "user", "content": "Hi"}]})
    assert response.status_code == 403
    assert not fake_llm.calls


async def test_status_does_not_fabricate_media(studio):
    data = (await studio.get("/api/studio")).json()
    assert data["photo_url"] is None and data["video_url"] is None
    assert data["media_available"] is False
    assert data["models"] == [MODEL]
    assert data["text_available"] is True


async def test_chat_exact_subscription_no_paid_fallback(studio, fake_llm):
    fake_llm.replies = ["Hello"]
    result = await studio.post("/api/studio/chat", json={
        "model": MODEL, "messages": [{"role": "user", "content": "Hi"}],
    })
    assert result.status_code == 200
    assert result.json()["text"] == "Hello"
    _, args = fake_llm.calls[-1]
    assert args == {"capability": "chat:fast", "model": MODEL, "workflow": "studio"}
    fake_llm.replies = [LLMError("private upstream details")]
    failed = await studio.post("/api/studio/chat", json={
        "model": MODEL, "messages": [{"role": "user", "content": "Hi"}],
    })
    assert failed.status_code == 502
    assert "private upstream" not in failed.text
    assert len(fake_llm.calls) == 2


@pytest.mark.parametrize("model,content", [("gemini/paid", "Hi"), (MODEL, " ")])
async def test_invalid_request_never_calls_provider(studio, fake_llm, model, content):
    response = await studio.post("/api/studio/chat", json={
        "model": model, "messages": [{"role": "user", "content": content}],
    })
    assert response.status_code in (409, 422)
    assert not fake_llm.calls


async def test_csrf_required(studio, fake_llm):
    del studio.headers["X-CSRF-Token"]
    response = await studio.post("/api/studio/chat", json={
        "model": MODEL, "messages": [{"role": "user", "content": "Hi"}],
    })
    assert response.status_code == 403
    assert not fake_llm.calls


async def test_asset_requires_containment(studio, container, tmp_path):
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private")
    container.settings.studio_photo_filename = str(outside)
    assert (await studio.get("/api/studio/assets/photo")).status_code == 404
    assert (await studio.get("/api/studio")).json()["photo_url"] is None


async def test_operator_cannot_access_studio(api, container, fake_llm):
    container.settings.studio_owner_telegram_id = OPERATOR[0]
    token = await login(api, OPERATOR[0])
    assert (await api.get("/api/studio")).status_code == 403
    assert (await api.get("/api/studio/assets/photo")).status_code == 403
    response = await api.post("/api/studio/chat", headers={"X-CSRF-Token": token}, json={
        "model": MODEL, "messages": [{"role": "user", "content": "Hi"}],
    })
    assert response.status_code == 403
    assert not fake_llm.calls


async def test_untested_model_is_not_usable(studio, fake_llm):
    async def models():
        return [{"id": MODEL, "available": True, "tested": False,
                 "billing_type": "subscription"}]
    fake_llm.list_models = models
    assert (await studio.get("/api/studio")).json()["models"] == []
    response = await studio.post("/api/studio/chat", json={
        "model": MODEL, "messages": [{"role": "user", "content": "Hi"}],
    })
    assert response.status_code == 409
    assert not fake_llm.calls
