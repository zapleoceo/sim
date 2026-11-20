"""Private Studio text queue receipts."""
import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_chat_operation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("admin_user.id", ondelete="SET NULL")),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id"), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("key_expires_at", sa.DateTime(), nullable=False),
        sa.Column("input_cipher", sa.Text()),
        sa.Column("result_cipher", sa.Text()),
    )
    for name in ("owner_id", "expires_at", "key_expires_at"):
        op.create_index(f"ix_studio_chat_operation_{name}", "studio_chat_operation", [name])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM studio_chat_operation")).scalar_one():
        raise RuntimeError("Cannot discard Studio chat receipts")
    op.drop_table("studio_chat_operation")
