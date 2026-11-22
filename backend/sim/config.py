"""Settings from the environment (prefix SIM_). Secrets have no working defaults."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    env: str = "dev"                                    # dev | test | prod
    # postgresql+psycopg://user:pass@host/db — SQLAlchemy and Procrastinate share it.
    database_url: str = "postgresql+psycopg://sim:sim@localhost:5432/sim"
    public_url: str = "http://localhost:8000"
    # Fernet key or comma-separated keys (first encrypts, all decrypt) for channel credentials.
    secret_key: str = ""
    media_dir: str = "/app/media"
    static_dir: str = "/app/static"
    session_ttl_h: int = 72
    cookie_secure: bool = True

    broker_url: str = "https://aib.zapleo.com"
    broker_project_key: str = ""
    broker_poll_budget_s: float = 300.0
    # One batch long poll per process for all Broker jobs (docs/broker.md §2a); off = poll each.
    broker_batch_wait: bool = True
    # In-flight Broker jobs per lane in one process, JSON in SIM_BROKER_MAX_INFLIGHT, merged over
    # the defaults in sim/adapters/broker/http.py (DEFAULT_INFLIGHT).
    broker_max_inflight: dict[str, int] = Field(default_factory=dict)
    broker_video_budget_s: float = 900.0

    # Dashboard login: Telegram Login Widget of this bot; admins upserted from the id list.
    tg_login_bot_token: str = ""
    tg_login_bot_username: str = ""
    superadmin_telegram_ids: str = ""

    # The userbot's api_id / api_hash / StringSession / proxy live encrypted in
    # channel.credentials_enc (JSON), not in the environment.
    tg_min_send_interval_s: float = 1.5

    # Instagram Direct (docs/channels.md): the session lives encrypted in the channel, like
    # Telegram's. Poll pace and send caps per account; a default proxy for accounts without one.
    ig_poll_interval_s: float = 120.0
    ig_hourly_cap: int = 150
    ig_daily_cap: int = 800
    ig_default_proxy: str = ""
    # Clips by links (TikTok, Reels, Shorts): the proxy yt-dlp goes through; empty = the
    # Instagram default proxy, if any (docs/reply.md, «Ролик по ссылке»).
    link_proxy: str = ""

    # WhatsApp through Evolution API v2 (infra/docker-compose.yml, profile `whatsapp`). Empty
    # URL or key = the service is not installed: the dashboard says so instead of failing.
    # `evolution_webhook_base` is where Evolution posts events, as seen from its container
    # (http://api:8000/api in compose); empty = `public_url` + /api.
    evolution_url: str = ""
    evolution_api_key: str = ""
    evolution_webhook_base: str = ""

    calls_enabled: bool = True
    call_max_seconds: int = 540
    call_ring_min_s: float = 3.0
    call_ring_max_s: float = 7.0

    # Context window budget for the reply model, in estimated tokens (docs/context.md).
    context_budget_tokens: int = 60_000
    context_keep_recent_events: int = 60

    @property
    def superadmin_ids(self) -> list[int]:
        return [int(x) for x in self.superadmin_telegram_ids.replace(" ", "").split(",") if x]

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def whatsapp_ready(self) -> bool:
        return bool(self.evolution_url and self.evolution_api_key)

    @property
    def evolution_hooks_base(self) -> str:
        return (self.evolution_webhook_base or f"{self.public_url.rstrip('/')}/api").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
