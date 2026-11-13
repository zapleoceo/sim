"""Persona birthday and home time zone: age, zodiac and local time are computed by code."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("birth_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Kyiv"))


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("timezone")
        batch.drop_column("birth_date")
