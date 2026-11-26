"""Her stickers (docs/stickers.md, docs/database.md):

- `persona_sticker` — the stickers her Telegram account has (installed sets and favourites),
  read by the telegram process; what she may send;
- `message.sticker_id` — which of them an outgoing sticker message is.

Downgrade drops both (her outgoing sticker messages stay, without the link).

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: tuple[str, ...] = ("persona_sticker",)


def tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE SEQUENCE persona_sticker_id_seq")
    op.create_table(
        "persona_sticker",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("set_id", sa.BigInteger(), nullable=False),
        sa.Column("set_access_hash", sa.BigInteger(), nullable=False),
        sa.Column("set_name", sa.String(100), nullable=False),
        sa.Column("set_title", sa.String(200), nullable=False),
        sa.Column("emoji", sa.String(32), nullable=False),
        sa.Column("faved", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", tstz(), nullable=False),
        sa.Column("updated_at", tstz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persona_sticker")),
        sa.ForeignKeyConstraint(["persona_id"], ["persona.id"],
                                name=op.f("fk_persona_sticker_persona_id_persona"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channel.id"],
                                name=op.f("fk_persona_sticker_channel_id_channel"),
                                ondelete="CASCADE"),
        sa.UniqueConstraint("channel_id", "doc_id", name=op.f("uq_persona_sticker_channel_id")),
    )
    op.create_index(op.f("ix_persona_sticker_persona_id"), "persona_sticker", ["persona_id"])
    op.add_column("message", sa.Column("sticker_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_message_sticker_id_persona_sticker"), "message",
                          "persona_sticker", ["sticker_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_message_sticker_id_persona_sticker"), "message",
                       type_="foreignkey")
    op.drop_column("message", "sticker_id")
    op.drop_index(op.f("ix_persona_sticker_persona_id"), table_name="persona_sticker")
    op.drop_table("persona_sticker")
    op.execute("DROP SEQUENCE IF EXISTS persona_sticker_id_seq")
