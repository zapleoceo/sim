"""Private queued test-chat operations."""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preview_operation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id"), unique=True, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("key_expires_at", sa.DateTime(), nullable=False),
        sa.Column("input_cipher", sa.Text()),
        sa.Column("result_cipher", sa.Text()),
    )
    op.create_index("ix_preview_operation_expires_at", "preview_operation", ["expires_at"])
    op.create_index("ix_preview_operation_key_expires_at", "preview_operation", ["key_expires_at"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM preview_operation")).scalar_one():
        raise RuntimeError("Cannot discard unexpired preview operation identities")
    op.drop_table("preview_operation")
