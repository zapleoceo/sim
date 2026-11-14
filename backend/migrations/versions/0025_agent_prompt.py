"""Persona's single authoritative prompt: a nullable text, NULL keeps the composed prompt.

No persona is switched by this migration: `agent_prompt` stays NULL (legacy mode) until the
owner saves a text in the admin."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("agent_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("agent_prompt")
