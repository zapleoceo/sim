"""Communication journal (`dialog_event`) and the summary of older messages (`dialog_summary`).

Both tables start empty: nothing is back-filled. Earlier changes stay in `funnel_event` and the
system log; a dialog's summary is written the first time its messages leave the context window."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dialog_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dialog_id", sa.Integer(), sa.ForeignKey("dialog.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("from_value", sa.Text(), server_default="", nullable=False),
        sa.Column("to_value", sa.Text(), server_default="", nullable=False),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("actor_name", sa.String(120), server_default="", nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_dialog_event_dialog_created", "dialog_event", ["dialog_id", "created_at", "id"]
    )
    op.create_table(
        "dialog_summary",
        sa.Column("dialog_id", sa.Integer(), sa.ForeignKey("dialog.id", ondelete="CASCADE"),
                  primary_key=True, autoincrement=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("covered_message_id", sa.Integer(), server_default="0", nullable=False),
        sa.Column("covered_until", sa.DateTime(), nullable=True),
        sa.Column("messages_covered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dialog_summary")
    op.drop_index("ix_dialog_event_dialog_created", "dialog_event")
    op.drop_table("dialog_event")
