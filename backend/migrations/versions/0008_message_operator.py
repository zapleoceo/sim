"""Historical operator attribution; legacy/phone messages remain unknown."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.add_column(sa.Column("operator_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("operator_name", sa.String(120), nullable=True))
        batch.create_foreign_key("fk_message_operator", "admin_user", ["operator_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_message_created_at_id", ["created_at", "id"])
        batch.create_index("ix_message_dialog_created_at_id", ["dialog_id", "created_at", "id"])


def downgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.drop_index("ix_message_dialog_created_at_id")
        batch.drop_index("ix_message_created_at_id")
        batch.drop_constraint("fk_message_operator", type_="foreignkey")
        batch.drop_column("operator_name")
        batch.drop_column("operator_id")
