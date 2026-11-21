"""API test fixtures: the real Litestar app over fake ports and the rolled-back test session."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from litestar.testing import AsyncTestClient
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.app import create_app
from sim.channels.instagram.login import InstagramLogins
from sim.channels.whatsapp.login import WhatsAppLogins
from sim.clock import FixedClock
from sim.config import Settings
from sim.telegram.login import TelegramLogins
from tests.api.fakes import FakeDeps
from tests.api.helpers import BOT_TOKEN, ApiClient, World, login, make_admin, seed_world
from tests.fakes.channels import FakeEvolution, FakeIgFactory
from tests.fakes.telegram import LoginClientFactory


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>SIM</title>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('sim')", encoding="utf-8")
    return root


@pytest.fixture
def settings(static_dir: Path) -> Settings:
    return Settings(
        _env_file=None, env="test", tg_login_bot_token=BOT_TOKEN,
        tg_login_bot_username="@sim_login_bot", superadmin_telegram_ids="",
        cookie_secure=False, static_dir=str(static_dir), session_ttl_h=72,
        secret_key=Fernet.generate_key().decode(),
    )


@pytest.fixture
def deps(db: AsyncSession, clock: FixedClock, settings: Settings) -> FakeDeps:
    return FakeDeps(settings=settings, session=db, clock=clock)


@pytest.fixture
def login_factory() -> LoginClientFactory:
    """Fake Telethon clients for the QR login of a Telegram account."""
    return LoginClientFactory()


@pytest.fixture
def logins(login_factory: LoginClientFactory) -> TelegramLogins:
    return TelegramLogins(login_factory, qr_image=lambda url: f"svg:{url}")


@pytest.fixture
def ig_factory() -> FakeIgFactory:
    """Fake instagrapi clients for the Instagram login."""
    return FakeIgFactory()


@pytest.fixture
def evolution() -> FakeEvolution:
    """A fake Evolution API (WhatsApp); answers are set per test."""
    return FakeEvolution()


@pytest.fixture
async def client(deps: FakeDeps, logins: TelegramLogins, ig_factory: FakeIgFactory,
                 evolution: FakeEvolution) -> AsyncIterator[ApiClient]:
    app = create_app(deps, logins=logins,  # type: ignore[arg-type]
                     instagram=InstagramLogins(ig_factory, attempt_s=5),
                     whatsapp=WhatsAppLogins(evolution, "http://api:8000/api"))
    async with AsyncTestClient(app=app) as test_client:
        yield test_client


@pytest.fixture
async def api(client: ApiClient, db: AsyncSession, clock: FixedClock) -> ApiClient:
    """A client logged in as an active admin, sending the CSRF header."""
    await make_admin(db)
    await login(client, clock)
    return client


@pytest.fixture
async def world(db: AsyncSession, clock: FixedClock) -> World:
    return await seed_world(db, clock)
