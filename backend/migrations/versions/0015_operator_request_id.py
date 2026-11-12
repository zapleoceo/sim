"""Idempotency key for operator sends; nullable for existing messages."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.add_column(sa.Column("client_request_id", sa.String(64), nullable=True))
        batch.create_unique_constraint(
            "uq_message_dialog_client_request", ["dialog_id", "client_request_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.drop_constraint("uq_message_dialog_client_request", type_="unique")
        batch.drop_column("client_request_id")
