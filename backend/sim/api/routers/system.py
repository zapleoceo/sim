"""Health and authenticated media."""
from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text

from sim.api.deps import App, authed

router = APIRouter(tags=["system"])

HEALTH_BROKER_TIMEOUT_S = 3.0


def app_version() -> str:
    try:
        return version("sim")
    except PackageNotFoundError:
        return "0.0.0"


@router.get("/api/health")
async def health(c: App):
    db_ok = broker_ok = False
    try:
        async with c.factory() as s:
            await s.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    try:
        await asyncio.wait_for(c.llm.list_models(), HEALTH_BROKER_TIMEOUT_S)
        broker_ok = True
    except Exception:
        broker_ok = False
    return {"ok": db_ok, "db": db_ok, "broker": broker_ok, "version": app_version()}


@router.get("/api/media/{name}", dependencies=[Depends(authed)])
async def media(name: str, c: App) -> FileResponse:
    path = c.media.path(name)
    if path is None:
        raise HTTPException(404, "Not found")
    return FileResponse(path)
