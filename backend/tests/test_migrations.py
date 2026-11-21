"""Migrations without a database: branch layout, the frozen enum values and table order of
0001_initial, and the pure helpers of the CI migration gate (scripts/ci_postgres_smoke.py).
The gate itself runs against the disposable PostgreSQL in CI."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Enum
from sqlalchemy.engine import make_url

from sim.db.models import FTS_INDEXES, Base
from sim.domain.agents import AGENTS, REGISTRY

BACKEND = Path(__file__).resolve().parents[1]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


initial = load("migration_0001", BACKEND / "migrations" / "versions" / "0001_initial.py")
video_note = load("migration_0002",
                  BACKEND / "migrations" / "versions" / "0002_video_note_generation.py")
# The enum values after every revision: 0001 as frozen, then what later revisions changed.
life = load("migration_0003", BACKEND / "migrations" / "versions" / "0003_persona_life.py")
leads = load("migration_0004", BACKEND / "migrations" / "versions" / "0004_personas_leads.py")
agents = load("migration_0005", BACKEND / "migrations" / "versions" / "0005_persona_agents.py")
attention = load("migration_0006", BACKEND / "migrations" / "versions" / "0006_attention.py")
day_memory = load("migration_0007", BACKEND / "migrations" / "versions" / "0007_day_memory.py")
ENUM_CHECKS = {**initial.ENUM_CHECKS, **video_note.ENUM_CHECKS, **life.ENUM_CHECKS,
               **leads.ENUM_CHECKS, **agents.ENUM_CHECKS, **attention.ENUM_CHECKS,
               **day_memory.ENUM_CHECKS}
# Tables in creation order over all revisions.
TABLES = (*initial.TABLES, *life.TABLES, *day_memory.TABLES)
smoke = load("ci_postgres_smoke", BACKEND / "scripts" / "ci_postgres_smoke.py")


@pytest.fixture(scope="module")
def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def test_two_branches_sim_and_procrastinate(scripts):
    assert len(scripts.get_heads()) == 2        # hence `alembic upgrade heads` everywhere
    sim_head = scripts.get_revision("sim@head")
    assert sim_head is not None and sim_head.revision == "0008"
    procrastinate_head = scripts.get_revision("procrastinate@head")
    assert procrastinate_head is not None
    assert procrastinate_head.revision.startswith("procrastinate_")


def test_sim_branch_starts_at_0001(scripts):
    bases = {scripts.get_revision(base).revision for base in scripts.get_bases()}
    assert "0001" in bases and "procrastinate_0000" in bases


def test_video_note_revision_follows_the_initial_one(scripts):
    revision = scripts.get_revision("0002")
    assert revision is not None and revision.down_revision == "0001"
    assert video_note.ENUM_CHECKS[("generation", "kind")][1][-1] == "video_note"
    assert set(video_note.PREVIOUS_KINDS) == set(initial.ENUM_CHECKS[("generation", "kind")][1])


def test_life_revision_follows_the_video_note_one(scripts):
    revision = scripts.get_revision("0003")
    assert revision is not None and revision.down_revision == "0002"
    purposes = life.ENUM_CHECKS[("llm_call", "purpose")][1]
    assert purposes[-1] == "life" and purposes[:-1] == life.PREVIOUS_PURPOSES
    assert initial.ENUM_CHECKS[("llm_call", "purpose")][1] == life.PREVIOUS_PURPOSES


def test_personas_leads_revision_follows_the_life_one(scripts):
    revision = scripts.get_revision("0004")
    assert revision is not None and revision.down_revision == "0003"
    purposes = leads.ENUM_CHECKS[("llm_call", "purpose")][1]
    assert purposes[-1] == "passport" and purposes[:-1] == leads.PREVIOUS_PURPOSES
    assert life.ENUM_CHECKS[("llm_call", "purpose")][1] == leads.PREVIOUS_PURPOSES
    # The old memory is moved into the passport before its column is dropped.
    assert "jsonb_array_elements(c.memory)" in leads.MEMORY_TO_PASSPORT


def test_agents_revision_follows_the_personas_leads_one(scripts):
    revision = scripts.get_revision("0005")
    assert revision is not None and revision.down_revision == "0004"
    # Every old model column moves to its agent («Портретист» and the later «Внимание» had no
    # column), and no column of the model is left behind.
    assert set(agents.COLUMN_AGENTS.values()) == {spec.key.value for spec in AGENTS} - {
        "appearance", "attention"}
    persona = Base.metadata.tables["persona"]
    assert "agents" in persona.columns
    assert not set(agents.COLUMN_AGENTS) & set(persona.columns.keys())
    for column, agent in agents.COLUMN_AGENTS.items():
        assert f"'{agent}', CASE WHEN btrim({column}) <> ''" in agents.MODELS_TO_AGENTS
        assert f"{column} = coalesce(agents -> '{agent}' ->> 'model', '')" in \
            agents.AGENTS_TO_MODELS
    assert "jsonb_strip_nulls" in agents.MODELS_TO_AGENTS


def test_attention_revision_follows_the_agents_one(scripts):
    revision = scripts.get_revision("0006")
    assert revision is not None and revision.down_revision == "0005"
    purposes = attention.ENUM_CHECKS[("llm_call", "purpose")][1]
    assert purposes[-1] == "attention" and purposes[:-1] == attention.PREVIOUS_PURPOSES
    assert leads.ENUM_CHECKS[("llm_call", "purpose")][1] == attention.PREVIOUS_PURPOSES
    kinds = attention.ENUM_CHECKS[("chat_event", "kind")][1]
    assert kinds[-1] == "attention" and kinds[:-1] == attention.PREVIOUS_KINDS
    assert initial.ENUM_CHECKS[("chat_event", "kind")][1] == attention.PREVIOUS_KINDS
    persona = Base.metadata.tables["persona"]
    assert {"attention", "attention_check_at"} <= set(persona.columns.keys())
    assert "attention_instant" in Base.metadata.tables["chat"].columns


def test_day_memory_revision_follows_the_attention_one(scripts):
    revision = scripts.get_revision("0007")
    assert revision is not None and revision.down_revision == "0006"
    assert day_memory.TABLES == ("chat_day", "chat_week")
    assert "day_notes" in Base.metadata.tables["chat"].columns
    # The full-text indexes of the migration are the models' ones, by name and column.
    model_indexes = {str(index.name): (index.table.name, index.expressions[0])
                     for index in FTS_INDEXES if index.table is not None}
    assert set(model_indexes) == set(day_memory.FTS_INDEXES)
    for name, (table, column) in day_memory.FTS_INDEXES.items():
        assert model_indexes[name][0] == table
        assert column in str(model_indexes[name][1])
    # The night agents get the subscription model and their new prompts.
    sql = day_memory.reset_night_agents_sql()
    for agent in day_memory.NIGHT_AGENTS:
        assert f"#- '{{{agent},model}}'" in sql and f"#- '{{{agent},prompt}}'" in sql
        assert REGISTRY[agent].model == "codex-sub/gpt-6.1-sol"
    assert sql.startswith("UPDATE persona SET agents = agents #- ")


def test_revisions_create_every_model_table_in_fk_order():
    assert set(TABLES) == set(Base.metadata.tables)
    position = {name: i for i, name in enumerate(TABLES)}
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.use_alter:
                continue                    # persona.avatar_media_id is added at the end
            target = fk.column.table.name
            assert position[target] < position[table.name], f"{table.name} -> {target}"


def test_enum_checks_after_all_revisions_match_the_enums():
    expected = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Enum):
                expected[(table.name, column.name)] = (column.type.name,
                                                       tuple(column.type.enums))
    assert expected == ENUM_CHECKS


@pytest.mark.parametrize(("definition", "values"), [
    ("CHECK (((kind)::text = 'telegram_user'::text))", {"telegram_user"}),
    ("CHECK (((status)::text = ANY ((ARRAY['new'::character varying, "
     "'connected'::character varying])::text[])))", {"new", "connected"}),
    ("CHECK ((x = 'it''s'::text))", {"it's"}),
])
def test_check_values(definition, values):
    assert smoke.check_values(definition) == values


def test_expected_checks_cover_every_enum_column():
    expected = smoke.expected_checks(Base.metadata)
    assert expected[("channel", "ck_channel_channelstatus")] == {
        "new", "connected", "error", "disabled"}
    assert ("chat_event", "ck_chat_event_eventkind") in expected
    assert len(expected) == len(ENUM_CHECKS)


def test_compare_checks_reports_missing_and_different():
    expected = {("t", "ck_t_a"): {"x", "y"}, ("t", "ck_t_b"): {"z"}}
    actual = {("t", "ck_t_a"): {"x"}, ("other", "ck_other"): {"q"}}
    problems = smoke.compare_checks(expected, actual)
    assert len(problems) == 2
    assert "ck_t_a" in problems[0] and "missing CHECK ck_t_b" in problems[1]
    assert smoke.compare_checks(expected, expected) == []


GOOD = {"SIM_PG_DISPOSABLE": "YES", "SIM_ENV": "test",
        "SIM_PG_TEST_URL": "postgresql+psycopg://u:p@127.0.0.1:5432/ci_sim_release_test"}


def test_require_disposable_accepts_the_ci_database():
    url = smoke.require_disposable(GOOD)
    assert url.database == "ci_sim_release_test"
    assert smoke.scratch_url(url).database == "ci_sim_release_test_migrations"


@pytest.mark.parametrize("override", [
    {"SIM_PG_DISPOSABLE": "no"},
    {"SIM_ENV": "prod"},
    {"SIM_PG_TEST_URL": ""},
    {"SIM_PG_TEST_URL": "postgresql+psycopg://u:p@db.example.com/sim"},
    {"SIM_PG_TEST_URL": "sqlite+aiosqlite:///x.db"},
])
def test_require_disposable_refuses_anything_else(override):
    with pytest.raises(SystemExit):
        smoke.require_disposable({**GOOD, **override})


def test_alembic_config_passes_the_url_to_env():
    cfg = smoke.alembic_config(make_url("postgresql+psycopg://u:secret@127.0.0.1/x"))
    assert cfg.attributes["database_url"] == "postgresql+psycopg://u:secret@127.0.0.1/x"


def test_no_refusals_revision_follows_the_day_memory_one(scripts):
    revision = scripts.get_revision("0008")
    assert revision is not None and revision.down_revision == "0007"
    assert "no_refusals" in Base.metadata.tables["chat"].columns
