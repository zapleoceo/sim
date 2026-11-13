"""Health and authenticated media."""
from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy import text

from sim.api.deps import App, authed
from sim.api.media_files import stored_file_response

router = APIRouter(tags=["system"])

HEALTH_BROKER_TIMEOUT_S = 3.0
READINESS_DB_TIMEOUT_S = 3.0


def app_version() -> str:
    try:
        return version("sim")
    except PackageNotFoundError:
        return "0.0.0"


async def database_ready(c: App) -> bool:
    try:
        async with asyncio.timeout(READINESS_DB_TIMEOUT_S), c.factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


@router.get("/api/live")
async def live():
    """Process liveness only; deliberately independent of DB and external providers."""
    return {"ok": True}


@router.get("/api/ready")
async def ready(c: App, response: Response):
    """DB-backed API readiness. Broker outages do not prevent operator access."""
    db_ok = await database_ready(c)
    response.status_code = 200 if db_ok else 503
    return {"ok": db_ok, "db": db_ok, "version": app_version()}


@router.get("/api/health")
async def health(c: App, response: Response):
    """Compatibility diagnostics: same readiness status, with an informational Broker probe."""
    db_ok, broker_ok = await database_ready(c), False
    try:
        await asyncio.wait_for(c.llm.list_models(), HEALTH_BROKER_TIMEOUT_S)
        broker_ok = True
    except Exception:
        broker_ok = False
    response.status_code = 200 if db_ok else 503
    return {"ok": db_ok, "db": db_ok, "broker": broker_ok, "version": app_version()}


@router.get("/api/media/{name}", dependencies=[Depends(authed)])
async def media(name: str, c: App) -> FileResponse:
    path = c.media.path(name)
    if path is None:
        raise HTTPException(404, "Not found")
    return stored_file_response(path, name)
