"""System trace log: trace_event table, trace_id on job and llm_call. Additive and reversible."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_event",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("dialog_id", sa.Integer(), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("llm_call_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_trace_event_started_at_id", "trace_event", ["started_at", "id"])
    op.create_index("ix_trace_event_trace_started", "trace_event", ["trace_id", "started_at"])
    op.create_index("ix_trace_event_kind_started", "trace_event", ["kind", "started_at"])
    op.create_index("ix_trace_event_status_started", "trace_event", ["status", "started_at"])
    op.create_index("ix_trace_event_client_id", "trace_event", ["client_id"])
    op.create_index("ix_trace_event_dialog_id", "trace_event", ["dialog_id"])
    op.create_index("ix_trace_event_job_id", "trace_event", ["job_id"])
    with op.batch_alter_table("job") as batch:
        batch.add_column(sa.Column("trace_id", sa.String(32), nullable=True))
    with op.batch_alter_table("llm_call") as batch:
        batch.add_column(sa.Column("trace_id", sa.String(32), nullable=True))
        batch.create_index("ix_llm_call_trace_id", ["trace_id"])


def downgrade() -> None:
    with op.batch_alter_table("llm_call") as batch:
        batch.drop_index("ix_llm_call_trace_id")
        batch.drop_column("trace_id")
    with op.batch_alter_table("job") as batch:
        batch.drop_column("trace_id")
    op.drop_table("trace_event")
