"""Permanent outbound send intent and independently committed success receipt."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No FK/cascade: independent commits must not lock the caller's message or lose history.
    op.create_table(
        "outbound_send_intent",
        sa.Column("message_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE outbound_send_intent IN ACCESS EXCLUSIVE MODE")
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM outbound_send_intent")).scalar_one()
    if count:
        raise RuntimeError("Cannot discard outbound send intents; retain delivery history")
    op.drop_table("outbound_send_intent")
