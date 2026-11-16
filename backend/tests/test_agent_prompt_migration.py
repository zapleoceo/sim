"""Migrations 0030 + 0031: the single prompt column, then legacy-to-unified data migration."""
import sqlite3

from alembic import command
from alembic.script import ScriptDirectory

from tests.test_migrations import alembic_config, columns, insert


def test_agent_prompt_single_head_upgrade_downgrade(tmp_path):
    db = tmp_path / "prompt-migration.db"
    cfg = alembic_config(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0031"]
    assert script.get_revision("0031").down_revision == "0030"
    assert script.get_revision("0030").down_revision == "0029"
    cfg.config_file_name = None
    command.upgrade(cfg, "0029")
    assert "agent_prompt" not in columns(db, "persona")
    with sqlite3.connect(db) as con:
        insert(con, "strategy", id=1, name="S", goal="цель", prompt="совет", offer="курс",
               partner_name="RCIPM", stage_playbook='{"engaged":"узнай"}', rules='["не дави"]')
        insert(con, "persona", id=1, name="Марина", description="Ты Марина.",
               prohibitions="не обещать доход\nне просить пароли", strategy_id=1)
        insert(con, "persona", id=2, name="Ева", description="Ты Ева.",
               prohibitions="", strategy_id=1)
    command.upgrade(cfg, "0030")
    assert "agent_prompt" in columns(db, "persona")
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT id, agent_prompt FROM persona ORDER BY id").fetchall()
        assert rows[0][1] is None  # 0030 doesn't populate
        assert rows[1][1] is None
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT id, agent_prompt FROM persona ORDER BY id").fetchall()
        assert rows[0][1] is not None and "Ты Марина." in rows[0][1]
        assert "не обещать доход" in rows[0][1]
        assert "СТРАТЕГИЯ" in rows[0][1] and "не дави" in rows[0][1]
        assert rows[1][1] is not None and "Ты Ева." in rows[1][1]
