"""`uvicorn sim.apps.api:create_app --factory`: the dashboard HTTP API.

The API defers tasks (operator messages, replies after AI is switched on), so the Procrastinate
app is opened for the lifetime of the HTTP app."""
from __future__ import annotations

from litestar import Litestar

from sim.api.app import create_app as build_api
from sim.apps import setup_logging
from sim.config import get_settings
from sim.services.deps import set_deps
from sim.wiring import wire


def create_app() -> Litestar:
    setup_logging()
    wired = wire(get_settings())
    set_deps(wired.deps)
    app = build_api(wired.deps)

    async def open_tasks() -> None:
        await wired.tasks_app.open_async()

    async def close_all() -> None:
        await wired.tasks_app.close_async()
        await wired.aclose()

    app.on_startup.append(open_tasks)
    app.on_shutdown.append(close_all)
    return app
