"""Persona phone calls: the persona answers incoming Telegram calls with her live voice."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("accept_calls", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("accept_calls")
