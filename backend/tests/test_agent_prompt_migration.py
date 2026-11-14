"""Migration 0025: the nullable single prompt is the one head and a reversible schema change."""
import sqlite3

from alembic import command
from alembic.script import ScriptDirectory

from tests.test_migrations import alembic_config, columns, insert


def test_agent_prompt_single_head_upgrade_downgrade(tmp_path):
    db = tmp_path / "prompt-migration.db"
    cfg = alembic_config(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0025"]
    assert script.get_revision("0025").down_revision == "0024"
    # Do not let Alembic fileConfig disable application loggers in later tests.
    cfg.config_file_name = None
    command.upgrade(cfg, "0024")
    assert "agent_prompt" not in columns(db, "persona")
    with sqlite3.connect(db) as con:
        insert(con, "persona", id=1, name="Марина", description="Ты Марина.")
    command.upgrade(cfg, "head")
    assert "agent_prompt" in columns(db, "persona")
    assert "is_game" not in columns(db, "persona")       # 0024 stays applied: the game is gone
    with sqlite3.connect(db) as con:
        # No persona is switched by the migration: she stays in legacy mode with her text.
        assert con.execute("SELECT agent_prompt, description FROM persona").fetchall() == [(None, "Ты Марина.")]
    command.downgrade(cfg, "0024")
    assert "agent_prompt" not in columns(db, "persona")
    command.upgrade(cfg, "head")
    assert "agent_prompt" in columns(db, "persona")
