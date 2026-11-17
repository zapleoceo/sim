"""Explicit incoming recognition states; existing messages remain untouched."""
import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message", sa.Column("media_status", sa.String(16), nullable=True))
    op.add_column("message", sa.Column("media_reason", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "media_reason")
    op.drop_column("message", "media_status")
