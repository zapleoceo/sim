"""History of persona prompts: every saved `agent_prompt` becomes a version.

Each persona that already has a prompt gets it as its first version, so the history starts with
the text in use at the time of the upgrade."""
import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

FIRST_NOTE = "версия на момент включения истории"


def upgrade() -> None:
    op.create_table(
        "persona_prompt_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("persona_id", sa.Integer(), sa.ForeignKey("persona.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author", sa.String(120), nullable=False, server_default=""),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_persona_prompt_version_persona", "persona_prompt_version", ["persona_id", "id"])
    op.execute(sa.text(
        "INSERT INTO persona_prompt_version (persona_id, text, author, note, created_at) "
        "SELECT id, agent_prompt, '', :note, CURRENT_TIMESTAMP FROM persona "
        "WHERE agent_prompt IS NOT NULL AND agent_prompt <> '' ORDER BY id"
    ).bindparams(note=FIRST_NOTE))


def downgrade() -> None:
    op.drop_index("ix_persona_prompt_version_persona", table_name="persona_prompt_version")
    op.drop_table("persona_prompt_version")
