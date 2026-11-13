"""Persona media gallery: photos, videos and video notes per persona."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "persona_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("persona_id", sa.Integer(), sa.ForeignKey("persona.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("storage_name", sa.String(120), nullable=False),
        sa.Column("caption", sa.Text(), server_default="", nullable=False),
        sa.Column("tags", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_persona_media_persona_id", "persona_media", ["persona_id"])


def downgrade() -> None:
    op.drop_index("ix_persona_media_persona_id", "persona_media")
    op.drop_table("persona_media")
