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

from sim.db.models import Base

BACKEND = Path(__file__).resolve().parents[1]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


initial = load("migration_0001", BACKEND / "migrations" / "versions" / "0001_initial.py")
smoke = load("ci_postgres_smoke", BACKEND / "scripts" / "ci_postgres_smoke.py")


@pytest.fixture(scope="module")
def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def test_two_branches_sim_and_procrastinate(scripts):
    assert len(scripts.get_heads()) == 2        # hence `alembic upgrade heads` everywhere
    sim_head = scripts.get_revision("sim@head")
    assert sim_head is not None and sim_head.revision == "0001"
    procrastinate_head = scripts.get_revision("procrastinate@head")
    assert procrastinate_head is not None
    assert procrastinate_head.revision.startswith("procrastinate_")


def test_sim_branch_starts_at_0001(scripts):
    bases = {scripts.get_revision(base).revision for base in scripts.get_bases()}
    assert "0001" in bases and "procrastinate_0000" in bases


def test_initial_creates_every_model_table_in_fk_order():
    assert set(initial.TABLES) == set(Base.metadata.tables)
    position = {name: i for i, name in enumerate(initial.TABLES)}
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.use_alter:
                continue                    # persona.avatar_media_id is added at the end
            target = fk.column.table.name
            assert position[target] < position[table.name], f"{table.name} -> {target}"


def test_initial_enum_checks_match_the_enums():
    expected = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Enum):
                expected[(table.name, column.name)] = (column.type.name,
                                                       tuple(column.type.enums))
    assert initial.ENUM_CHECKS == expected


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
    assert len(expected) == len(initial.ENUM_CHECKS)


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
