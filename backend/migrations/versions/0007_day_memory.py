"""The day memory (docs/memory.md, docs/database.md):
- tables `chat_day` (the chronicler's summary of one persona-local day of a chat) and
  `chat_week` (the rollup of a week's days), UNIQUE per chat and day / week;
- `chat.day_notes` (JSONB, «заметки дня» and the compressed morning per local day);
- Russian full-text GIN indexes on `message.text` and `chat_day.summary` («А помнишь…?»);
- the night agents' new defaults: the chronicler («Летописец», `memory`) and the passport
  writer («Паспортистка», `passport`) run on the subscription model `codex-sub/gpt-6.1-sol` with
  new prompts for the new tasks, so their stored model and prompt overrides are reset (the key
  tier and max_tokens stay). `chat.summary` stays as the legacy «Давняя переписка».

Mirrors `Base.metadata.create_all()` like 0001-0006: BIGINT ids fed by `<table>_id_seq`, CHECK
names `ck_<table>_<enum class>`, Advanced Alchemy names for pk_, uq_, fk_. Downgrade drops the
tables, the indexes and the column (day summaries are lost); the reset overrides do not come
back (the agents then use their registry defaults).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: tuple[str, ...] = ("chat_day", "chat_week")
# (table, date column) of the two diary tables.
DIARY: dict[str, str] = {"chat_day": "day", "chat_week": "week"}

# (table, column) -> (enum name, allowed values) added by this revision; with 0001-0006 this is
# the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("chat_day", "source"): ("daysource", ("model", "edited")),
    ("chat_week", "source"): ("daysource", ("model", "edited")),
}

# The full-text indexes: name -> (table, column). The expression matches sim/db/models.py.
FTS_INDEXES: dict[str, tuple[str, str]] = {
    "ix_fts_message_text": ("message", "text"),
    "ix_fts_chat_day_summary": ("chat_day", "summary"),
}

# Agents whose stored `model` and `prompt` overrides are reset to the new defaults.
NIGHT_AGENTS: tuple[str, ...] = ("memory", "passport")

JSON_LIST = sa.text("'[]'::jsonb")
JSON_DICT = sa.text("'{}'::jsonb")


def tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def reset_night_agents_sql() -> str:
    """One UPDATE: drop `model` and `prompt` from the night agents' overrides, keep the rest.
    `#-` on a path that is not there changes nothing; an agent left with nothing stored is
    removed by `strip_empty_agents_sql`."""
    paths = " ".join(f"#- '{{{agent},{key}}}'" for agent in NIGHT_AGENTS
                     for key in ("model", "prompt"))
    return f"UPDATE persona SET agents = agents {paths}"


def strip_empty_agents_sql() -> str:
    return ("UPDATE persona SET agents = (SELECT coalesce(jsonb_object_agg(key, value), "
            "'{}'::jsonb) FROM jsonb_each(agents) WHERE value <> '{}'::jsonb)")


def diary_table(table: str) -> None:
    period = DIARY[table]
    name, values = ENUM_CHECKS[(table, "source")]
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(period, sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("facts", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("first_event_id", sa.BigInteger(), nullable=True),
        sa.Column("last_event_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", tstz(), nullable=False),
        sa.Column("updated_at", tstz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.id"], name=op.f(f"fk_{table}_chat_id_chat"),
                                ondelete="CASCADE"),
        sa.UniqueConstraint("chat_id", period, name=op.f(f"uq_{table}_chat_id")),
        sa.CheckConstraint(f"source IN ({allowed(values)})", name=op.f(f"ck_{table}_{name}")),
    )


def upgrade() -> None:
    op.add_column("chat", sa.Column("day_notes", JSONB(), server_default=JSON_DICT,
                                    nullable=False))
    for table in TABLES:
        op.execute(f"CREATE SEQUENCE {table}_id_seq")
        diary_table(table)
    for index, (table, column) in FTS_INDEXES.items():
        op.create_index(index, table, [sa.text(f"to_tsvector('russian'::regconfig, {column})")],
                        postgresql_using="gin")
    op.execute(reset_night_agents_sql())
    op.execute(strip_empty_agents_sql())


def downgrade() -> None:
    for index, (table, _) in FTS_INDEXES.items():
        op.drop_index(index, table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
        op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
    op.drop_column("chat", "day_notes")
