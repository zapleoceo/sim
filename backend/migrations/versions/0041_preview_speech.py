"""Durable speech stage for private preview operations."""
import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preview_speech",
        sa.Column("operation_id", sa.String(64), sa.ForeignKey("preview_operation.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("broker_key", sa.String(64), nullable=False, unique=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("broker_id", sa.BigInteger()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_cipher", sa.Text()),
        sa.Column("audio_cipher", sa.Text()),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM preview_speech")).scalar_one():
        raise RuntimeError("Cannot discard preview speech receipts")
    op.drop_table("preview_speech")
