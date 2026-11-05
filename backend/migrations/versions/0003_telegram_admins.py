"""Telegram-only admin login: admin_user gets telegram identity, loses password; adds
used_login_hash and login_link.

Existing admin rows are password-only bootstrap stubs (owner-approved): they and their
sessions are deleted. Works on PostgreSQL and SQLite (batch mode).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Password-only admins have no Telegram identity and could never sign in again.
    op.execute("DELETE FROM auth_session")
    op.execute("UPDATE dialog SET operator_id = NULL WHERE operator_id IS NOT NULL")
    op.execute("DELETE FROM admin_user")

    with op.batch_alter_table("admin_user", schema=None) as batch_op:
        batch_op.drop_constraint("uq_admin_user_username", type_="unique")
        batch_op.drop_column("username")
        batch_op.drop_column("password_hash")
        batch_op.add_column(sa.Column("telegram_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("tg_username", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("display_name", sa.String(length=120), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("photo_url", sa.Text(), nullable=False, server_default=""))
        batch_op.create_unique_constraint("uq_admin_user_telegram_id", ["telegram_id"])
        batch_op.create_unique_constraint("uq_admin_user_tg_username", ["tg_username"])

    op.create_table(
        "used_login_hash",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_used_login_hash")),
    )
    op.create_index(
        op.f("ix_used_login_hash_expires_at"), "used_login_hash", ["expires_at"], unique=False
    )
    op.create_table(
        "login_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["admin_user.id"],
            name=op.f("fk_login_link_user_id_admin_user"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_link")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_link_token_hash")),
    )


def downgrade() -> None:
    op.drop_table("login_link")
    op.drop_index(op.f("ix_used_login_hash_expires_at"), table_name="used_login_hash")
    op.drop_table("used_login_hash")
    op.execute("DELETE FROM auth_session")
    op.execute("UPDATE dialog SET operator_id = NULL WHERE operator_id IS NOT NULL")
    op.execute("DELETE FROM admin_user")
    with op.batch_alter_table("admin_user", schema=None) as batch_op:
        batch_op.drop_constraint("uq_admin_user_tg_username", type_="unique")
        batch_op.drop_constraint("uq_admin_user_telegram_id", type_="unique")
        batch_op.drop_column("photo_url")
        batch_op.drop_column("display_name")
        batch_op.drop_column("tg_username")
        batch_op.drop_column("telegram_id")
        batch_op.add_column(sa.Column("username", sa.String(length=64), nullable=False))
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=False))
        batch_op.create_unique_constraint("uq_admin_user_username", ["username"])
