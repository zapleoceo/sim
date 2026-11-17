"""Dormant private Studio operation receipts; no activation or owner binding."""
import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("studio_operation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("admin_user.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("result_encrypted", sa.Text(), nullable=True))
    op.create_index("ix_studio_operation_owner_id", "studio_operation", ["owner_id"])


def downgrade() -> None:
    op.drop_table("studio_operation")
