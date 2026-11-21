"""Alembic environment (async engine, psycopg).

Two branches live side by side (docs/deploy.md): SIM's own revisions (label `sim`,
migrations/versions) and Procrastinate's job-queue revisions (label `procrastinate`, shipped in
`procrastinate.contrib.alembic`). `alembic upgrade heads` applies both.

The database URL is `config.attributes["database_url"]` when a caller passes one (the CI smoke
does), otherwise SIM_DATABASE_URL through sim.config."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from sim.config import get_settings
from sim.db.models import FTS_INDEX_PREFIX, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

PROCRASTINATE_PREFIX = "procrastinate_"


def include_name(name: str | None, type_: str, parent_names: Any) -> bool:
    """Procrastinate's tables belong to its own branch: autogenerate must not want to drop them.
    The full-text GIN indexes (`ix_fts_*`) are compared by name in the CI gate, not here:
    PostgreSQL prints their expression back in its own words."""
    if type_ == "index":
        return not (name or "").startswith(FTS_INDEX_PREFIX)
    return type_ != "table" or not (name or "").startswith(PROCRASTINATE_PREFIX)


def include_object(obj: Any, name: str | None, type_: str, reflected: bool,
                   compare_to: Any) -> bool:
    """The models' side of `include_name` for the full-text indexes."""
    return type_ != "index" or not (name or "").startswith(FTS_INDEX_PREFIX)


def database_url() -> str:
    return str(config.attributes.get("database_url") or get_settings().database_url)


def configure(**kw: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # Procrastinate revisions commit inside an autocommit block; Alembic recommends one
        # transaction per revision around them.
        transaction_per_migration=True,
        **kw,
    )


def run_migrations_offline() -> None:
    configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
