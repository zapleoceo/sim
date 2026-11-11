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

    # Both switches are required: enabling the feature alone never releases a backlog.
    ig_content_enabled: bool = False
    ig_content_post_ids: tuple[int, ...] = ()  # JSON array in SIM_IG_CONTENT_POST_IDS

    ig_proxy: str = ""
    ig_poll_interval_s: int = 120

    # Telegram userbot (personal account, MTProto)
    tg_user_poll_interval_s: int = 20
    tg_user_min_send_interval_s: float = 3.0   # anti-ban: min gap between sends per account

    partner_webhook_secret: str = ""

    # Admin login is Telegram-only (Login Widget; the bot needs the site domain via BotFather
    # /setdomain). Superadmins are upserted from the comma list on seed / API start.
    tg_login_bot_token: str = ""
    tg_login_bot_username: str = ""                    # without @
    superadmin_telegram_ids: str = ""

    # Pre-send guard: a DIFFERENT broker rotation than the generator (chat:sales / persona.model);
    # when the guard is served by the generator's own model it is re-run on the fallback.
    guard_capability: str = "structured"
    guard_fallback_capability: str = "prefilter"

    reply_debounce_s: int = 8          # wait for a burst of client messages before answering
    worker_tick_s: float = 2.0
    # System log (trace_event): the worker deletes steps older than this; 0 keeps everything.
    trace_retention_days: int = 30
    trace_purge_interval_s: float = 3600.0

    static_dir: str = "../frontend/dist/sim/browser"   # built Angular app (prod: /app/static)
    media_dir: str = "./media"  # downloaded voice/images (prod: /app/media)
    # Private subscription studio: explicit account binding, disabled by default.
    studio_owner_telegram_id: int = 0
    studio_photo_filename: str = ""
    studio_video_filename: str = ""


def parse_telegram_ids(spec: str) -> list[int]:
    """Comma list of numeric Telegram ids; junk items are skipped."""
    return [int(x) for x in (i.strip() for i in spec.split(",")) if x.isdigit()]


@lru_cache
def settings() -> Settings:
    return Settings()
