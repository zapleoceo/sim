"""The persona's life (docs/persona-life.md, docs/database.md):
- `persona.life` (routine, people, hobbies, places), `persona.model_life` (the daily planner),
  `persona.appearance` (her exact look for image models), `persona.voice_ambience_enabled`;
- `generation.scene` — the scene a photo / video was made in (continuity references);
- tables `persona_arc` (story arcs), `persona_day` (planned days), `persona_canon` (facts she
  invented about herself);
- `llm_call.purpose` allows `life`.

Mirrors what `Base.metadata.create_all()` emits, like 0001: BIGINT ids fed by
`<table>_id_seq`, CHECK constraints named `ck_<table>_<enum class lowercase>`, Advanced Alchemy
names for pk_, uq_, fk_, ix_. Downgrade drops all of it (planned days, arcs and canon are lost;
`life` rows in `llm_call` are deleted before the old CHECK comes back).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: tuple[str, ...] = ("persona_arc", "persona_day", "persona_canon")

# (table, column) -> (enum name, allowed values) added or changed by this revision; with
# 0001 and 0002 this is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("llm_call", "purpose"): ("llmpurpose", (
        "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
        "memory", "life")),
    ("persona_arc", "status"): ("arcstatus", ("active", "paused", "done")),
    ("persona_day", "source"): ("daysource", ("model", "edited")),
    ("persona_canon", "status"): ("canonstatus", ("active", "rejected")),
}
PREVIOUS_PURPOSES: tuple[str, ...] = (
    "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
    "memory")

JSON_LIST = sa.text("'[]'::jsonb")
JSON_DICT = sa.text("'{}'::jsonb")
EMPTY = sa.text("''")


def tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def pk_column() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False)


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", tstz(), nullable=False),
        sa.Column("updated_at", tstz(), nullable=False),
    ]


def enum_column(column: str) -> sa.Column:
    return sa.Column(column, sa.String(32), nullable=False)


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def enum_check(table: str, column: str) -> sa.CheckConstraint:
    name, values = ENUM_CHECKS[(table, column)]
    return sa.CheckConstraint(f"{column} IN ({allowed(values)})", name=op.f(f"ck_{table}_{name}"))


def fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete)


def pk(table: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}"))


def _replace_purpose_check(values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[("llm_call", "purpose")]
    constraint = op.f(f"ck_llm_call_{name}")
    op.drop_constraint(constraint, "llm_call", type_="check")
    op.create_check_constraint(constraint, "llm_call", f"purpose IN ({allowed(values)})")


def upgrade() -> None:
    op.add_column("persona", sa.Column("life", JSONB(), server_default=JSON_DICT,
                                       nullable=False))
    op.add_column("persona", sa.Column("model_life", sa.String(160), server_default=EMPTY,
                                       nullable=False))
    op.add_column("persona", sa.Column("appearance", sa.Text(), server_default=EMPTY,
                                       nullable=False))
    op.add_column("persona", sa.Column("voice_ambience_enabled", sa.Boolean(),
                                       server_default=sa.text("true"), nullable=False))
    op.add_column("generation", sa.Column("scene", JSONB(), server_default=JSON_DICT,
                                          nullable=False))
    _replace_purpose_check(ENUM_CHECKS[("llm_call", "purpose")][1])

    for table in TABLES:
        op.execute(f"CREATE SEQUENCE {table}_id_seq")

    op.create_table(
        "persona_arc",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        enum_column("status"),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        *audit_columns(),
        pk("persona_arc"),
        fk("persona_arc", "persona_id", "persona", "CASCADE"),
        enum_check("persona_arc", "status"),
    )
    op.create_index(op.f("ix_persona_arc_persona_id"), "persona_arc", ["persona_id"])

    op.create_table(
        "persona_day",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("events", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        enum_column("source"),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *audit_columns(),
        pk("persona_day"),
        fk("persona_day", "persona_id", "persona", "CASCADE"),
        sa.UniqueConstraint("persona_id", "day", name=op.f("uq_persona_day_persona_id")),
        enum_check("persona_day", "source"),
    )

    op.create_table(
        "persona_canon",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        enum_column("status"),
        *audit_columns(),
        pk("persona_canon"),
        fk("persona_canon", "persona_id", "persona", "CASCADE"),
        fk("persona_canon", "chat_id", "chat", "SET NULL"),
        enum_check("persona_canon", "status"),
    )
    op.create_index(op.f("ix_persona_canon_persona_id"), "persona_canon", ["persona_id"])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    for table in reversed(TABLES):
        op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
    op.execute("DELETE FROM llm_call WHERE purpose = 'life'")
    _replace_purpose_check(PREVIOUS_PURPOSES)
    op.drop_column("generation", "scene")
    for column in ("voice_ambience_enabled", "appearance", "model_life", "life"):
        op.drop_column("persona", column)
