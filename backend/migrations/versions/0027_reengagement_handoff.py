"""Versioned contextual reengagement and durable handoff marks."""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dialog", sa.Column("activity_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("dialog", sa.Column("reengagement_plan", sa.JSON(), nullable=True))
    op.add_column("message", sa.Column("handoff_reason", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("handoff_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "handoff_at")
    op.drop_column("message", "handoff_reason")
    op.drop_column("dialog", "reengagement_plan")
    op.drop_column("dialog", "activity_revision")
