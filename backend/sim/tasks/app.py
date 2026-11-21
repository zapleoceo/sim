"""The Procrastinate app shared by all processes. It uses the same database as SQLAlchemy
(`SIM_DATABASE_URL`), through its own psycopg pool."""
from __future__ import annotations

import procrastinate

from sim.config import Settings
from sim.tasks.agent import register_agent_tasks

_SQLALCHEMY_SCHEMES = ("postgresql+psycopg://", "postgresql+psycopg_async://",
                       "postgres+psycopg://")


def conninfo(database_url: str) -> str:
    """`postgresql+psycopg://...` (SQLAlchemy) -> `postgresql://...` (libpq)."""
    for scheme in _SQLALCHEMY_SCHEMES:
        if database_url.startswith(scheme):
            return "postgresql://" + database_url[len(scheme):]
    return database_url


def make_app(settings: Settings) -> procrastinate.App:
    """Tasks of other processes (e.g. `telegram.send_message`) are deferred by name; the
    process that runs them registers them on this app at start-up."""
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=conninfo(settings.database_url)))
    register_agent_tasks(app)
    # Telegram tasks run only in the telegram process (queue `telegram`), but every process
    # knows them so a defer by name is checked against one registry.
    from sim.telegram.tasks import NAMESPACE, blueprint

    app.add_tasks_from(blueprint, namespace=NAMESPACE)
    return app
