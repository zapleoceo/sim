import pytest

from sim.config import settings


@pytest.fixture(autouse=True)
def channel_env(monkeypatch):
    monkeypatch.setenv("SIM_PUBLIC_URL", "https://sim.example.com")
    monkeypatch.setenv("SIM_EVOLUTION_URL", "http://evo.test")
    monkeypatch.setenv("SIM_EVOLUTION_API_KEY", "global-key")
    monkeypatch.setenv("SIM_EVOLUTION_WEBHOOK_BASE", "http://api:8000")
    monkeypatch.setenv("SIM_IG_PROXY", "")
    settings.cache_clear()
    yield
    settings.cache_clear()
