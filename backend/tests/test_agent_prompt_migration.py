"""Migrations 0030-0033: the single prompt column, legacy-to-unified data, then the prompt as
the only place of her text (no description, prohibitions, age; no routine copy)."""
import sqlite3

from alembic import command
from alembic.script import ScriptDirectory

from tests.test_migrations import alembic_config, columns, insert


def test_agent_prompt_single_head_upgrade_downgrade(tmp_path):
    db = tmp_path / "prompt-migration.db"
    cfg = alembic_config(db)
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision("0033").down_revision == "0032"
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


def test_0033_keeps_every_text_in_the_prompt_and_drops_the_duplicate_fields(tmp_path):
    db = tmp_path / "prompt-only.db"
    cfg = alembic_config(db)
    cfg.config_file_name = None
    command.upgrade(cfg, "0032")
    routine = "пн-пт 09:00-13:00 консультации\nежедневно 23:00-07:00 сплю"
    copy = "РАСПОРЯДОК НЕДЕЛИ (сейчас ты находишься в соответствующей строке):\n" + routine
    with sqlite3.connect(db) as con:
        # 1: 0031's prompt with the routine copy; 2: an empty prompt filled from the old fields;
        # 3: a copy of the routine with the routine field empty stays (the only place of it).
        insert(con, "persona", id=1, name="Марина", description="старое", routine=routine,
               agent_prompt=f"Ты Марина.\n\n{copy}\n\nСТРАТЕГИЯ\n- Цель: регистрация")
        insert(con, "persona", id=2, name="Ева", description="Ты Ева, психолог.",
               prohibitions="не обещать доход", agent_prompt="")
        insert(con, "persona", id=3, name="Оля", description="", routine="",
               agent_prompt=f"Ты Оля.\n\n{copy}")
    command.upgrade(cfg, "0033")
    cols = columns(db, "persona")
    assert not {"description", "prohibitions", "age"} & cols and {"agent_prompt", "birth_date"} <= cols
    with sqlite3.connect(db) as con:
        prompts = dict(con.execute("SELECT id, agent_prompt FROM persona"))
    assert prompts[1] == "Ты Марина.\n\nСТРАТЕГИЯ\n- Цель: регистрация"
    assert prompts[2] == "Ты Ева, психолог.\n\nЧего нельзя никогда:\n- не обещать доход"
    assert prompts[3] == f"Ты Оля.\n\n{copy}"
    with sqlite3.connect(db) as con:
        versions = dict(con.execute("SELECT persona_id, text FROM persona_prompt_version"))
    assert versions == {1: prompts[1], 2: prompts[2]}    # the changed prompts end their history
    command.downgrade(cfg, "0032")
    assert {"description", "prohibitions", "age"} <= columns(db, "persona")
    with sqlite3.connect(db) as con:
        assert dict(con.execute("SELECT id, agent_prompt FROM persona"))[2].startswith("Ты Ева")
    command.upgrade(cfg, "head")
