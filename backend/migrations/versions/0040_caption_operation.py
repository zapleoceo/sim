"""Scope asynchronous caption operations to their saved post."""
import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("preview_operation", sa.Column("post_id", sa.Integer(), nullable=True))
    op.add_column("preview_operation", sa.Column("draft_revision", sa.String(64), nullable=True))
    op.create_index(
        "uq_preview_caption_revision", "preview_operation", ["owner_id", "post_id", "draft_revision"], unique=True,
    )
    op.create_table(
        "caption_operation_alias",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("operation_id", sa.String(64),
                  sa.ForeignKey("preview_operation.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_caption_operation_alias_operation_id", "caption_operation_alias", ["operation_id"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT count(*) FROM preview_operation WHERE post_id IS NOT NULL"
    )).scalar_one():
        raise RuntimeError("Cannot discard active caption operation identities")
    op.drop_table("caption_operation_alias")
    op.drop_index("uq_preview_caption_revision", table_name="preview_operation")
    op.drop_column("preview_operation", "draft_revision")
    op.drop_column("preview_operation", "post_id")
