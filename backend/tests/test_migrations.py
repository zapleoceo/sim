"""Alembic 0003 on SQLite: password-only admins are removed, new Telegram schema is in place."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parent.parent


def alembic_config(db: Path) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")
    return cfg


def columns(db: Path, table: str) -> set[str]:
    with sqlite3.connect(db) as con:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def test_0003_drops_password_admins_and_adds_telegram_columns(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "0002")
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO admin_user (id, username, password_hash, role, is_active, created_at) "
            "VALUES (1, 'dima', 'argon2-hash', 'owner', 1, '2026-11-01 00:00:00')"
        )
        con.execute(
            "INSERT INTO auth_session (user_id, token_hash, csrf_token, expires_at, created_at) "
            "VALUES (1, 'h', 'c', '2030-01-01 00:00:00', '2026-11-01 00:00:00')"
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM admin_user").fetchone() == (0,)
        assert con.execute("SELECT count(*) FROM auth_session").fetchone() == (0,)
        con.execute(
            "INSERT INTO admin_user (telegram_id, tg_username, role, is_active, created_at) "
            "VALUES (169510539, 'owner', 'superadmin', 1, '2026-11-07 00:00:00')"
        )
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = columns(db, "admin_user")
    assert {"telegram_id", "tg_username", "display_name", "photo_url", "role"} <= cols
    assert not {"username", "password_hash"} & cols
    assert {"used_login_hash", "login_link"} <= tables
    # telegram_id is unique
    with sqlite3.connect(db) as con:
        try:
            con.execute(
                "INSERT INTO admin_user (telegram_id, role, is_active, created_at) "
                "VALUES (169510539, 'operator', 1, '2026-11-07 00:00:00')"
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("telegram_id must be unique")


def test_0003_downgrade_roundtrip(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")
    assert {"username", "password_hash"} <= columns(db, "admin_user")
