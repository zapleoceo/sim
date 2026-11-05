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


def test_0004_folds_persona_fields_into_description_and_adds_guard_columns(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "0003")
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO persona (id, name, gender, avatar_url, tagline, character, backstory, "
            "communication_style, language, model, voice_id, is_active, is_virtual_disclosed, "
            "disclosure_text, created_at) VALUES (1, 'Марина', 'female', '', 'Слоган А', "
            "'Тёплая', 'Двадцать лет в проектах', 'Коротко', 'ru', '', '', 1, 1, 'd', "
            "'2026-11-01 00:00:00')"
        )
        con.execute(
            "INSERT INTO persona (id, name, gender, avatar_url, tagline, character, backstory, "
            "communication_style, language, model, voice_id, is_active, is_virtual_disclosed, "
            "disclosure_text, created_at) VALUES (2, 'Пустая', 'female', '', '', '', 'Только био', "
            "'', 'ru', '', '', 1, 1, '', '2026-11-01 00:00:00')"
        )
    command.upgrade(cfg, "0004")
    cols = columns(db, "persona")
    assert {"description", "prohibitions"} <= cols
    assert not {"tagline", "character", "backstory", "communication_style"} & cols
    assert {"guard_verdict", "guard_violations", "guard_model", "original_text"} <= columns(db, "message")
    with sqlite3.connect(db) as con:
        rows = dict(con.execute("SELECT id, description FROM persona"))
        assert con.execute("SELECT prohibitions FROM persona WHERE id = 1").fetchone() == ("",)
        assert con.execute("SELECT name, disclosure_text FROM persona WHERE id = 1").fetchone() == ("Марина", "d")
    assert rows[1] == (
        "Слоган: Слоган А\n\nХарактер: Тёплая\n\nБиография: Двадцать лет в проектах\n\n"
        "Стиль общения: Коротко"
    )
    assert rows[2] == "Биография: Только био"          # empty sections are skipped


def test_0004_downgrade_roundtrip(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003")
    assert {"tagline", "character", "backstory", "communication_style"} <= columns(db, "persona")
    assert "guard_verdict" not in columns(db, "message")


def test_0005_adds_strategy_prompt_and_simulation_tables_and_roundtrips(tmp_path):
    db = tmp_path / "mig5.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "0004")
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO strategy (id, name, goal, offer, partner_name, registration_url, stage_playbook, "
            "rules, followup_hours, reply_delay_s, notes, created_at) VALUES "
            "(1, 'S', '', '', '', '', '{}', '[]', '[]', '[20, 90]', '', '2026-11-01 00:00:00')"
        )
    command.upgrade(cfg, "head")
    assert "prompt" in columns(db, "strategy")
    assert {"admin_user_id", "persona_id", "stage", "status", "needs", "next_step", "notes", "closed_at"} <= columns(db, "sim_branch")
    assert {"branch_id", "role", "guard_verdict", "guard_violations", "model"} <= columns(db, "sim_message")
    assert "debug" in columns(db, "sim_admin_pref")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT prompt FROM strategy WHERE id = 1").fetchone() == ("",)
    command.downgrade(cfg, "0004")
    assert "prompt" not in columns(db, "strategy") and not columns(db, "sim_branch")
    command.upgrade(cfg, "head")
