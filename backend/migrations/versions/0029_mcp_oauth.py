"""Owner-consented single-use OAuth codes and audience-bound MCP grants."""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("oauth_client_id", "issuer", "resource"):
        op.add_column("mcp_grant", sa.Column(name, sa.Text(), nullable=True))
    op.create_table("mcp_authorization",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nonce_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("code_hash", sa.String(64), unique=True, nullable=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("auth_session.id", ondelete="CASCADE"), nullable=False),
        *(sa.Column(n, sa.Text(), nullable=False) for n in ("client_id", "redirect_uri", "issuer", "resource", "state")),
        sa.Column("challenge", sa.String(43), nullable=False),
        *(sa.Column(n, sa.JSON(), nullable=False) for n in ("scopes", "dialog_ids", "post_ids")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consented", sa.Boolean(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_table("mcp_authorization")
    for name in ("resource", "issuer", "oauth_client_id"):
        op.drop_column("mcp_grant", name)
