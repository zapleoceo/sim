"""Urgent dialogs: replies in a dialog an operator marked urgent never wait for the persona's
routine (sim.services.reply_schedule)."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dialog") as batch:
        batch.add_column(sa.Column("urgent", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("dialog") as batch:
        batch.drop_column("urgent")
