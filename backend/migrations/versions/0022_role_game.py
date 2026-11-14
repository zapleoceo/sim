"""The role game: the game persona flag, and the game state and consent date of a dialog."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("is_game", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("dialog") as batch:
        batch.add_column(sa.Column("game_state", sa.String(length=8), nullable=False,
                                   server_default=""))
        batch.add_column(sa.Column("game_consent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dialog") as batch:
        batch.drop_column("game_consent_at")
        batch.drop_column("game_state")
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("is_game")
