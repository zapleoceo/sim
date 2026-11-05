"""FastAPI app factory: `uvicorn sim.api.main:create_app --factory`."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from sim.adapters.db.session import create_all
from sim.api.routers import auth, catalog, channels, content, hooks, inbox, reports, system
from sim.config import Settings
from sim.config import settings as get_settings
from sim.wiring import Container, build_container

log = logging.getLogger(__name__)
NOT_SPA_PREFIXES = ("api/", "hooks/")


def resolve_static_dir(configured: str) -> Path | None:
    path = Path(configured)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path if (path / "index.html").is_file() else None


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built Angular app; unknown non-API paths fall back to index.html."""
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(NOT_SPA_PREFIXES) or path in ("api", "hooks"):
            raise HTTPException(404, "Not found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


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
        if cfg.bootstrap_owner_username and cfg.bootstrap_owner_password:
            async with c.factory() as s:
                created = await c.auth(s).bootstrap_owner(
                    cfg.bootstrap_owner_username, cfg.bootstrap_owner_password
                )
                await s.commit()
            if created:
                log.info("bootstrap owner %r created", created.username)
        yield
        if owns_container:
            await c.engine.dispose()

    app = FastAPI(title="Sim", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    for router in (
        system.router, auth.router, catalog.personas, catalog.strategies, catalog.models,
        channels.router, inbox.dialogs, inbox.clients, inbox.funnel, content.router,
        reports.analytics, reports.partner, hooks.router,
    ):
        app.include_router(router)
    if (static := resolve_static_dir(cfg.static_dir)) is not None:
        mount_spa(app, static)
    return app
