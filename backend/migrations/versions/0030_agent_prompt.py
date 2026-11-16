"""Optional unified persona instructions; existing personas remain in legacy mode."""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("persona", sa.Column("agent_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("persona", "agent_prompt")
