"""FastAPI app factory: `uvicorn sim.api.main:create_app --factory`."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from sim.adapters.db.session import create_all, transaction
from sim.api.routers import (
    activity,
    admins,
    auth,
    catalog,
    channels,
    content,
    hooks,
    inbox,
    reports,
    simulations,
    studio,
    system,
)
from sim.config import Settings, parse_telegram_ids
from sim.config import settings as get_settings
from sim.services.admin_bot import admin_bot_secret, register_webhook
from sim.wiring import Container, build_container

log = logging.getLogger(__name__)
NOT_SPA_PREFIXES = ("api/", "hooks/")


def resolve_static_dir(configured: str) -> Path | None:
    path = Path(configured)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path if (path / "index.html").is_file() else None


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built admin SPA; unknown non-API paths fall back to index.html."""
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(NOT_SPA_PREFIXES) or path in ("api", "hooks"):
            raise HTTPException(404, "Not found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


async def register_admin_bot_webhook(c: Container) -> None:
    """Point the login bot's webhook at this API (production only; idempotent). A failure is
    logged without the token and never stops the API: the Login Widget does not depend on it."""
    cfg = c.settings
    transport = c.admin_transport()
    if cfg.env != "prod" or transport is None:
        return
    secret = admin_bot_secret(cfg.secret_key, cfg.tg_login_bot_token)
    try:
        await register_webhook(transport, cfg.public_url, secret)
    except Exception as exc:  # TelegramError text never includes the token
        log.warning("admin bot setWebhook failed: %s: %s", type(exc).__name__, exc)
    else:
        log.info("admin bot webhook registered")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    cfg = settings or (container.settings if container else get_settings())
    owns_container = container is None
    box: dict[str, Container] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container or build_container(cfg)
        box["c"] = c
        app.state.container = c
        if cfg.env != "prod" and cfg.database_url.startswith("sqlite"):
            await create_all(c.engine)
        if ids := parse_telegram_ids(cfg.superadmin_telegram_ids):
            async with transaction(c.factory) as s:
                if changed := await c.auth(s).sync_superadmins(ids):
                    log.info("superadmins synced from settings: %d changed", changed)
        await register_admin_bot_webhook(c)
        yield
        if owns_container:
            await c.aclose()

    # The schema lists every internal route: public only outside production.
    docs = cfg.env != "prod"
    app = FastAPI(
        title="Sim",
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
        lifespan=lifespan,
    )
    for router in (
        system.router, auth.router, admins.router, catalog.personas, catalog.strategies, catalog.models,
        channels.router, inbox.dialogs, inbox.clients, inbox.funnel, content.router,
        reports.analytics, reports.partner, simulations.router, hooks.router, activity.router, studio.router,
    ):
        app.include_router(router)
    if (static := resolve_static_dir(cfg.static_dir)) is not None:
        mount_spa(app, static)
    return app
