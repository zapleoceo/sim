"""«Звонит сама» on by default (docs/initiative.md): a call is her own tool, like a message —
she decides when to call (the owner, 2026-11-22). `chat.calls_out` defaults to true and every
existing chat gets it; the switch now only turns it off for one chat. Downgrade restores the
default false (the values stay).

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("chat", "calls_out", server_default=sa.text("true"))
    op.execute("UPDATE chat SET calls_out = true")


def downgrade() -> None:
    op.alter_column("chat", "calls_out", server_default=sa.text("false"))
