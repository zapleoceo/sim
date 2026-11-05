"""Persona editor: one `description` + `prohibitions` replace tagline/character/backstory/
communication_style; message gets the pre-send guard columns.

The old fields are folded into `description` as readable sections before they are dropped.
Works on PostgreSQL and SQLite (batch mode).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SECTIONS = (
    ("tagline", "Слоган"),
    ("character", "Характер"),
    ("backstory", "Биография"),
    ("communication_style", "Стиль общения"),
)


def fold_description(row: dict[str, str | None]) -> str:
    parts = [f"{label}: {row[col].strip()}" for col, label in SECTIONS if (row[col] or "").strip()]
    return "\n\n".join(parts)


def upgrade() -> None:
    with op.batch_alter_table("persona", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("prohibitions", sa.Text(), nullable=False, server_default=""))

    conn = op.get_bind()
    persona = sa.table(
        "persona",
        sa.column("id", sa.Integer),
        sa.column("description", sa.Text),
        *(sa.column(col, sa.Text) for col, _ in SECTIONS),
    )
    rows = conn.execute(sa.select(persona)).mappings().all()
    for row in rows:
        conn.execute(
            sa.update(persona).where(persona.c.id == row["id"]).values(
                description=fold_description(dict(row))
            )
        )

    with op.batch_alter_table("persona", schema=None) as batch_op:
        for col, _ in SECTIONS:
            batch_op.drop_column(col)

    with op.batch_alter_table("message", schema=None) as batch_op:
        batch_op.add_column(sa.Column("guard_verdict", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("guard_violations", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("guard_model", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("original_text", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("message", schema=None) as batch_op:
        batch_op.drop_column("original_text")
        batch_op.drop_column("guard_model")
        batch_op.drop_column("guard_violations")
        batch_op.drop_column("guard_verdict")

    with op.batch_alter_table("persona", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tagline", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("character", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("backstory", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("communication_style", sa.Text(), nullable=False, server_default="")
        )
    # The free text goes back into `character` (the sections cannot be told apart again).
    op.execute("UPDATE persona SET character = description")
    with op.batch_alter_table("persona", schema=None) as batch_op:
        batch_op.drop_column("prohibitions")
        batch_op.drop_column("description")
