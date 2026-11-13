"""The independent prompt release must have one head and a reversible schema change."""
from alembic import command
from alembic.script import ScriptDirectory

from tests.test_migrations import alembic_config, columns


def test_agent_prompt_single_head_upgrade_downgrade(tmp_path):
    db = tmp_path / "prompt-migration.db"
    cfg = alembic_config(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0019_agent_prompt"]
    # Do not let Alembic fileConfig disable application loggers in later tests.
    cfg.config_file_name = None
    command.upgrade(cfg, "0019")
    assert "agent_prompt" not in columns(db, "persona")
    command.upgrade(cfg, "head")
    assert "agent_prompt" in columns(db, "persona")
    command.downgrade(cfg, "0019")
    assert "agent_prompt" not in columns(db, "persona")
    command.upgrade(cfg, "head")
    assert "agent_prompt" in columns(db, "persona")
