"""Persona weekly routine: where she is now, was and will be is computed by code."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("routine", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("routine")
