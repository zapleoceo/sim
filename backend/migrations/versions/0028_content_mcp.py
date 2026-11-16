"""Scoped, expiring content MCP grants and metadata-only audit."""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("mcp_grant",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("auth_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("dialog_ids", sa.JSON(), nullable=False),
        sa.Column("post_ids", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False))
    op.create_table("mcp_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("grant_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("reason_hash", sa.String(64), nullable=True),
        sa.Column("before_hash", sa.String(64), nullable=True),
        sa.Column("after_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade() -> None:
    op.drop_table("mcp_audit")
    op.drop_table("mcp_grant")
