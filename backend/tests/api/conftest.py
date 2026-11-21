"""API test fixtures: the real Litestar app over fake ports and the rolled-back test session."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from litestar.testing import AsyncTestClient
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.app import create_app
from sim.clock import FixedClock
from sim.config import Settings
from tests.api.fakes import FakeDeps
from tests.api.helpers import BOT_TOKEN, ApiClient, World, login, make_admin, seed_world


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
    )


@pytest.fixture
def deps(db: AsyncSession, clock: FixedClock, settings: Settings) -> FakeDeps:
    return FakeDeps(settings=settings, session=db, clock=clock)


@pytest.fixture
async def client(deps: FakeDeps) -> AsyncIterator[ApiClient]:
    async with AsyncTestClient(app=create_app(deps)) as test_client:  # type: ignore[arg-type]
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
