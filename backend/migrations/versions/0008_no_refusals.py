"""«Без отказов» in a chat (docs/writing.md, docs/database.md):

- `chat.no_refusals` — this client gets what he asks for: what she can not do right now she does
  later. Off by default.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat", sa.Column("no_refusals", sa.Boolean(),
                                    server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    op.drop_column("chat", "no_refusals")
