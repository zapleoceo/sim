"""Lead passport: one text per client kept by the passportist agent (sim.services.passport)."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("client") as batch:
        batch.add_column(sa.Column("passport", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("passport_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("client") as batch:
        batch.drop_column("passport_updated_at")
        batch.drop_column("passport")
