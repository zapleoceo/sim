"""Explicit owner-selected identity and subscription reserve models; disabled by default."""
import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("identity_model", "fallback_model"):
        op.add_column("persona", sa.Column(name, sa.String(120), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("persona", "fallback_model")
    op.drop_column("persona", "identity_model")
