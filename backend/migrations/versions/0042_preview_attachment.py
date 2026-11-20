"""Private attachment recognition operations."""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preview_attachment_operation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="SET NULL")),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("key_expires_at", sa.DateTime(), nullable=False),
        sa.Column("broker_id", sa.String(128)),
        sa.Column("input_cipher", sa.Text()),
        sa.Column("result_cipher", sa.Text()),
    )
    for name in ("status", "expires_at", "key_expires_at"):
        op.create_index(f"ix_preview_attachment_operation_{name}", "preview_attachment_operation", [name])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM preview_attachment_operation")).scalar_one():
        raise RuntimeError("Cannot discard preview attachment operation history")
    op.drop_table("preview_attachment_operation")
