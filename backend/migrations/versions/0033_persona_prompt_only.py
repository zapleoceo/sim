"""One place for every persona fact: the prompt holds the text, the fields hold the rest.

- `description` and `prohibitions` are gone: since 0031 their text lives in `agent_prompt`. A
  persona whose prompt is still empty gets it from them first, so no text is lost.
- `age` is gone: the age is computed from `birth_date` (sim.services.persona_clock).
- The copy of the routine that 0031 wrote into the prompt is removed when it is exactly the
  block made from the `routine` field: code adds the routine to every reply itself, so the copy
  was a second, stale place of the same schedule.
- `agent_prompt` is NOT NULL, "" by default.
- A prompt this migration changed gets a new version in its history (0032), so the history
  still ends with the text in use.

Downgrade brings the columns back empty (the text stays in `agent_prompt`)."""
import re
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

ROUTINE_PREFIX = "РАСПОРЯДОК НЕДЕЛИ (сейчас ты находишься в соответствующей строке):\n"
PROHIBITIONS_HEADING = "Чего нельзя никогда"

persona_t = sa.table(
    "persona",
    sa.column("id", sa.Integer),
    sa.column("name", sa.Text),
    sa.column("description", sa.Text),
    sa.column("prohibitions", sa.Text),
    sa.column("routine", sa.Text),
    sa.column("agent_prompt", sa.Text),
)

version_t = sa.table(
    "persona_prompt_version",
    sa.column("persona_id", sa.Integer),
    sa.column("text", sa.Text),
    sa.column("author", sa.String),
    sa.column("note", sa.String),
    sa.column("created_at", sa.DateTime),
)
VERSION_NOTE = "поля описания и запретов перенесены в промпт, копия распорядка убрана"


def prohibitions_block(text: str) -> str:
    rules = [r for line in (text or "").splitlines()
             if (r := re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", line).strip())]
    return f"{PROHIBITIONS_HEADING}:\n" + "\n".join(f"- {rule}" for rule in rules) if rules else ""


def prompt_from_fields(name: str, description: str, prohibitions: str) -> str:
    head = (description or "").strip() or f"Ты {name}."
    return "\n\n".join(p for p in (head, prohibitions_block(prohibitions)) if p)


def without_routine_copy(prompt: str, routine: str) -> str:
    """The prompt without the routine paragraph 0031 copied into it (it starts with the code's
    own heading, which the owner never types). Kept when the `routine` field is empty: then the
    copy is the only place of her schedule."""
    if not (routine or "").strip():
        return prompt
    parts = prompt.split("\n\n")
    kept = [p for p in parts if not p.strip().startswith(ROUTINE_PREFIX.strip())]
    return "\n\n".join(kept) if len(kept) != len(parts) else prompt


def upgrade() -> None:
    conn = op.get_bind()
    for row in conn.execute(sa.select(persona_t)).fetchall():
        prompt = row.agent_prompt or ""
        if not prompt.strip():
            prompt = prompt_from_fields(row.name, row.description, row.prohibitions)
        prompt = without_routine_copy(prompt, row.routine)
        if prompt != row.agent_prompt:
            conn.execute(persona_t.update().where(persona_t.c.id == row.id).values(agent_prompt=prompt))
            # The changed text is the newest version of her prompt history (0032).
            conn.execute(version_t.insert().values(
                persona_id=row.id, text=prompt, author="", note=VERSION_NOTE,
                created_at=datetime.now(UTC).replace(tzinfo=None)))
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("description")
        batch.drop_column("prohibitions")
        batch.drop_column("age")
        batch.alter_column("agent_prompt", existing_type=sa.Text(), nullable=False,
                           server_default="")


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.alter_column("agent_prompt", existing_type=sa.Text(), nullable=True,
                           server_default=None)
        batch.add_column(sa.Column("age", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("prohibitions", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
