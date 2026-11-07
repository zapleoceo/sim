"""Permanent publication intent and receipt, independent of caller transactions."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No FK/cascade: preserve history and avoid locking the caller's post.
    op.create_table(
        "content_publish_intent",
        sa.Column("post_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # Operators must stop publication workers before any schema rollback.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE content_publish_intent IN ACCESS EXCLUSIVE MODE")
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM content_publish_intent")).scalar_one()
    if count:
        raise RuntimeError("Cannot discard publication intents; retain history and disable publication")
    op.drop_table("content_publish_intent")
