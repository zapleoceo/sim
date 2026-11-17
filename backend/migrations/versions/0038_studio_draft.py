"""Private encrypted Studio drafts, never queued operations."""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("studio_draft",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("admin_user.id"), primary_key=True),
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("text_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM studio_draft")).scalar_one():
        raise RuntimeError("Cannot discard Studio drafts or revision tombstones")
    op.drop_table("studio_draft")
