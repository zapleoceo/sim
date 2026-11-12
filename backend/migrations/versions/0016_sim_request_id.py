"""Idempotency key for browser-origin admin simulations."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sim_message") as batch:
        batch.add_column(sa.Column("client_request_id", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_sim_message_branch_request", ["branch_id", "client_request_id"])


def downgrade() -> None:
    with op.batch_alter_table("sim_message") as batch:
        batch.drop_constraint("uq_sim_message_branch_request", type_="unique")
        batch.drop_column("client_request_id")
