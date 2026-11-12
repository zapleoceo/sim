"""Persona voice ambience: voice notes "recorded on a phone" in a room, on the street, in a cafe.

`off` keeps the clean TTS sound (the behaviour before this migration)."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("voice_ambience", sa.String(32), nullable=False,
                                   server_default="off"))


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("voice_ambience")
