"""Many personas and the client passport (docs/personas.md, docs/leads.md, docs/database.md):
- `persona.is_active` (archive instead of delete), `persona.model_passport` (the passport writer);
- `client.passport` (JSONB, the living profile of a lead) replaces `client.memory`: every
  remembered fact moves into the passport text before the column is dropped;
- `llm_call.purpose` allows `passport`.

Mirrors `Base.metadata.create_all()` like 0001-0003: CHECK names `ck_<table>_<enum class>`,
server defaults equal to the models'. Downgrade brings `client.memory` back with the passport
text as one fact (the structured fields are lost), deletes `passport` rows of `llm_call` before
the old CHECK returns and drops the new persona columns.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) -> (enum name, allowed values) changed by this revision; with 0001-0003 this
# is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("llm_call", "purpose"): ("llmpurpose", (
        "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
        "memory", "life", "passport")),
}
PREVIOUS_PURPOSES: tuple[str, ...] = (
    "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
    "memory", "life")

JSON_LIST = sa.text("'[]'::jsonb")
JSON_DICT = sa.text("'{}'::jsonb")
EMPTY = sa.text("''")

# Old memory facts become the first section of the passport text (one fact per line).
MEMORY_TO_PASSPORT = r"""
UPDATE client SET passport = jsonb_build_object(
    'text', 'Что известно (перенесено из памяти):' || E'\n' || facts.lines,
    'stage', 'new', 'temperature', 0, 'locked_fields', '[]'::jsonb)
FROM (
    SELECT c.id, string_agg('- ' || (item.value ->> 'fact'), E'\n' ORDER BY item.ordinality)
        AS lines
    FROM client c, jsonb_array_elements(c.memory) WITH ORDINALITY AS item(value, ordinality)
    WHERE jsonb_typeof(c.memory) = 'array'
      AND coalesce(btrim(item.value ->> 'fact'), '') <> ''
    GROUP BY c.id
) AS facts
WHERE client.id = facts.id
"""

PASSPORT_TO_MEMORY = """
UPDATE client SET memory = jsonb_build_array(jsonb_build_object(
    'fact', passport ->> 'text', 'event_id', NULL, 'at', passport ->> 'updated_at'))
WHERE coalesce(btrim(passport ->> 'text'), '') <> ''
"""


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_purpose_check(values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[("llm_call", "purpose")]
    constraint = op.f(f"ck_llm_call_{name}")
    op.drop_constraint(constraint, "llm_call", type_="check")
    op.create_check_constraint(constraint, "llm_call", f"purpose IN ({allowed(values)})")


def upgrade() -> None:
    op.add_column("persona", sa.Column("model_passport", sa.String(160), server_default=EMPTY,
                                       nullable=False))
    op.add_column("persona", sa.Column("is_active", sa.Boolean(),
                                       server_default=sa.text("true"), nullable=False))
    op.add_column("client", sa.Column("passport", JSONB(), server_default=JSON_DICT,
                                      nullable=False))
    op.execute(MEMORY_TO_PASSPORT)
    op.drop_column("client", "memory")
    _replace_purpose_check(ENUM_CHECKS[("llm_call", "purpose")][1])


def downgrade() -> None:
    op.execute("DELETE FROM llm_call WHERE purpose = 'passport'")
    _replace_purpose_check(PREVIOUS_PURPOSES)
    op.add_column("client", sa.Column("memory", JSONB(), server_default=JSON_LIST,
                                      nullable=False))
    op.execute(PASSPORT_TO_MEMORY)
    op.drop_column("client", "passport")
    for column in ("is_active", "model_passport"):
        op.drop_column("persona", column)
