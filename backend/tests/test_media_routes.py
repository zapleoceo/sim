"""Dormant preferences and auth; synthetic, no provider calls."""
import pytest

from sim.adapters.db.session import transaction
from sim.services.app_settings import AppSettingsService, SettingError
from sim.services.media_routes import fallback_candidate, route_plan
from tests.conftest import OPERATOR, OWNER, login

KEYS = {"studio_photo_subscription_model": "codex-sub/synthetic",
        "studio_photo_google_model": "gemini/synthetic-image",
        "studio_video_model": "gemini/gemini-omni-1.2-flash"}


async def test_persistence_reset_without_activation(container):
    async with transaction(container.factory) as session:
        svc = AppSettingsService(session)
        original = await svc.load()
        for key, value in KEYS.items():
            assert getattr(original, key) == ""
            await svc.set(key, value, None)
    async with transaction(container.factory) as session:
        svc = AppSettingsService(session)
        settings = await svc.load()
        plan = route_plan(settings)
        assert [row["kind"] for row in plan] == ["photo", "video_note", "video", "video_call"]
        assert all(row["available"] is False for row in plan)
        assert plan[0]["chain"][0]["model"] == KEYS["studio_photo_subscription_model"]
        assert plan[0]["chain"][1]["model"] == KEYS["studio_photo_google_model"]
        assert settings.persona_photo_model == original.persona_photo_model
        assert settings.incoming_media_subscription == 0
        for key in KEYS:
            await svc.reset(key)
        reset = await svc.load()
        assert all(getattr(reset, key) == "" for key in KEYS)


@pytest.mark.parametrize("key,value", [
    ("studio_photo_subscription_model", "gemini/paid"),
    ("studio_photo_google_model", "codex-sub/synthetic"),
    ("studio_video_model", "gemini/veo-3.1-generate-preview"),
    ("studio_video_model", "vivix/w1"),
    ("studio_photo_subscription_model", "https://secret.example"),
])
async def test_invalid_route_not_stored(container, key, value):
    async with transaction(container.factory) as session:
        svc = AppSettingsService(session)
        with pytest.raises(SettingError):
            await svc.set(key, value, None)
        assert (await svc.item(key)).value == ""


@pytest.mark.parametrize("outcome", ["unknown", "timeout", "lost_receipt", "pending", "done",
                                      "failed", "budget", "safety", "permission", "http_503"])
def test_no_fallback_for_uncertain_or_denied(outcome):
    assert not fallback_candidate(outcome, terminal=True, receipt_reconciled=True,
                                  target_verified=True, budget_authorized=True)


@pytest.mark.parametrize("missing", [None, "terminal", "receipt_reconciled",
                                     "target_verified", "budget_authorized"])
def test_candidate_requires_all_evidence(missing):
    evidence = dict(terminal=True, receipt_reconciled=True,
                    target_verified=True, budget_authorized=True)
    if missing:
        evidence[missing] = False
    assert fallback_candidate("unavailable_not_accepted", **evidence) is (missing is None)


async def test_owner_settings_and_personal_plan_never_generate(api, container, fake_llm):
    token = await login(api, OWNER[0])
    api.headers["X-CSRF-Token"] = token
    for key, value in KEYS.items():
        assert (await api.put(f"/api/settings/{key}", json={"value": value})).status_code == 200
    assert (await api.get("/api/studio")).json()["route_plan"] == []
    container.settings.studio_owner_telegram_id = OWNER[0]
    data = (await api.get("/api/studio")).json()
    assert len(data["route_plan"]) == 4
    assert data["media_available"] is False and data["capabilities_verified"] is False
    assert not fake_llm.calls
    del api.headers["X-CSRF-Token"]
    assert (await api.put("/api/settings/studio_video_model", json={"value": ""})).status_code == 403


async def test_operator_cannot_change_routes_or_read_studio(api):
    token = await login(api, OPERATOR[0])
    api.headers["X-CSRF-Token"] = token
    assert (await api.get("/api/studio")).status_code == 403
    assert (await api.put("/api/settings/studio_video_model", json={"value": ""})).status_code == 403
