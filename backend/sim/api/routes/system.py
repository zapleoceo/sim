"""Outside `/api`: `/healthz` (process alive), `/readyz` (database reachable) and the admin
SPA (built frontend in `settings.static_dir`; unknown client routes get `index.html`)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from litestar import Response, get
from litestar.exceptions import NotFoundException
from litestar.response import File
from sqlalchemy import text

from sim.api.context import ApiContext
from sim.api.errors import error_response

API_PREFIX = "/api/"


@get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@get("/readyz")
async def readyz(ctx: ApiContext) -> Response[dict[str, str]]:
    try:
        async with ctx.deps.sessions() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return error_response(503, "База данных недоступна")
    return Response({"status": "ok"})


def static_file(root: Path, spa_path: str) -> Path | None:
    """The file under `root` for this URL path; None when it does not exist or escapes."""
    relative = spa_path.strip("/")
    if not relative:
        return None
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return None
    return candidate


@get(["/", "/{spa_path:path}"], include_in_schema=False)
async def spa(ctx: ApiContext, spa_path: str | None = None) -> File:
    """Built assets as files; any other path is a client route of the SPA -> index.html.
    Unknown `/api/...` paths stay a JSON 404."""
    path = "/" + (spa_path or "").lstrip("/")
    if path.startswith(API_PREFIX) or path == "/api":
        raise NotFoundException(detail=f"Нет такого адреса: {path}")
    root = Path(ctx.settings.static_dir)
    found = static_file(root, path)
    if found is not None:
        return File(path=found, content_disposition_type="inline")
    return File(path=root / "index.html", media_type="text/html; charset=utf-8",
                content_disposition_type="inline", headers={"Cache-Control": "no-cache"})


def static_routes(static_dir: str) -> list[Any]:
    """The SPA route only when the frontend is built into `static_dir`."""
    return [spa] if (Path(static_dir) / "index.html").is_file() else []


ROUTES = [healthz, readyz]
