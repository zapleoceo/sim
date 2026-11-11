"""Owner-editable settings (prompt texts, tunables) that override code defaults; the persona
"are you a bot" disclosure fields are removed (the honesty floor lives in the prompt code) and
so is the strategy's artificial reply delay."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_by_id",
            sa.Integer(),
            sa.ForeignKey("admin_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("disclosure_text")
        batch.drop_column("is_virtual_disclosed")
    with op.batch_alter_table("strategy") as batch:
        batch.drop_column("reply_delay_s")  # replies go out right after generation


def downgrade() -> None:
    with op.batch_alter_table("strategy") as batch:
        batch.add_column(
            sa.Column("reply_delay_s", sa.JSON(), nullable=False, server_default="[20, 90]")
        )
    with op.batch_alter_table("persona") as batch:
        batch.add_column(
            sa.Column("is_virtual_disclosed", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("disclosure_text", sa.Text(), nullable=False, server_default=""))
    op.drop_table("app_setting")
