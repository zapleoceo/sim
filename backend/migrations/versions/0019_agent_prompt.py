"""Optional single authoritative agent prompt; preserve existing personas unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("persona", sa.Column("agent_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("persona", "agent_prompt")
