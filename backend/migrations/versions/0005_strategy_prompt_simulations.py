"""Strategy free-text prompt and the admin simulation tables (login-bot chat with a persona).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("strategy", schema=None) as batch_op:
        batch_op.add_column(sa.Column("prompt", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "sim_branch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_user_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("persona_id", sa.Integer(), sa.ForeignKey("persona.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_active_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False, server_default="first_contact"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("needs", sa.JSON(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_sim_branch_admin_user_id", "sim_branch", ["admin_user_id"])
    op.create_table(
        "sim_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("sim_branch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("guard_verdict", sa.String(16), nullable=True),
        sa.Column("guard_violations", sa.JSON(), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sim_message_branch_id_id", "sim_message", ["branch_id", "id"])
    op.create_table(
        "sim_admin_pref",
        sa.Column("admin_user_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("debug", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_table("sim_admin_pref")
    op.drop_index("ix_sim_message_branch_id_id", table_name="sim_message")
    op.drop_table("sim_message")
    op.drop_index("ix_sim_branch_admin_user_id", table_name="sim_branch")
    op.drop_table("sim_branch")
    with op.batch_alter_table("strategy", schema=None) as batch_op:
        batch_op.drop_column("prompt")
