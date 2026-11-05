"""Settings from env (prefix SIM_). Secrets never have defaults that work in production."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    env: str = "dev"                                   # dev | test | prod
    database_url: str = "sqlite+aiosqlite:///./sim.db"
    public_url: str = "http://localhost:8000"          # used for Telegram webhook URLs
    # Fernet key or comma-separated list (first encrypts, all decrypt) for channel secrets.
    secret_key: str = ""
    session_ttl_h: int = 72
    cookie_secure: bool = True
    # Peers whose X-Real-IP / X-Forwarded-For we believe (nginx + docker gateway); comma list of IPs/CIDRs.
    trusted_proxies: str = "127.0.0.1,::1,172.16.0.0/12"

    broker_url: str = "https://aib.zapleo.com"
    broker_project_key: str = ""
    broker_poll_budget_s: float = 180.0
    broker_subscription_budget_s: float = 600.0

    evolution_url: str = "http://evolution:8080"
    evolution_api_key: str = ""
    # Address Evolution uses to call us back inside the docker network.
    evolution_webhook_base: str = "http://api:8000"

    ig_proxy: str = ""
    ig_poll_interval_s: int = 120

    partner_webhook_secret: str = ""

    bootstrap_owner_username: str = ""
    bootstrap_owner_password: str = ""

    reply_debounce_s: int = 8          # wait for a burst of client messages before answering
    worker_tick_s: float = 2.0

    static_dir: str = "../frontend/dist/sim/browser"   # built Angular app (prod: /app/static)
    media_dir: str = "./media"                         # downloaded voice/images (prod: /app/media)


@lru_cache
def settings() -> Settings:
    return Settings()
