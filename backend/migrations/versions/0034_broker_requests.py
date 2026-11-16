"""Durable, encrypted Broker requests; no FK to a worker's locked job."""
import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_request",
        sa.Column("job_id", sa.BigInteger(), primary_key=True),
        sa.Column("call_index", sa.Integer(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("request_cipher", sa.Text(), nullable=False),
        sa.Column("response_cipher", sa.Text()),
        sa.Column("remote_id", sa.String(80)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM broker_request")).scalar_one():
        raise RuntimeError("Cannot discard Broker recovery history; reconcile and retain it")
    op.drop_table("broker_request")
