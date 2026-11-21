"""The persona's agents (docs/agents.md, docs/database.md): `persona.agents` (JSONB,
`{agent_key: {model, key_tier, prompt, max_tokens}}`) replaces the eleven `persona.model_*`
columns. Every chosen model moves into `agents` under its agent's key before the columns are
dropped; an empty column stays out (the agent's registry default and fallback apply, as before:
the guard and the chronicler borrow the companion's model, the passport writer the
chronicler's). An empty `model_life` gets the planner's registry default
(`codex-sub/gpt-6.1-sol`), as the owner asked for the daily plan on the subscription.

Mirrors `Base.metadata.create_all()` like 0001-0004 (server default `'{}'::jsonb`). Downgrade
brings the columns back (`''` server default, like 0003/0004) filled from `agents` models;
tiers, prompts and max_tokens are lost.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision changes no enum CHECK; with 0001-0004 the frozen enum copy stays complete.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}

# Old column -> agent key (sim/domain/agents.py, frozen here).
COLUMN_AGENTS: dict[str, str] = {
    "model_reply": "reply",
    "model_guard": "guard",
    "model_vision": "vision",
    "model_transcribe": "transcribe",
    "model_memory": "memory",
    "model_tts": "tts",
    "model_image": "image",
    "model_video": "video",
    "model_live": "live",
    "model_life": "life",
    "model_passport": "passport",
}

JSON_DICT = sa.text("'{}'::jsonb")
EMPTY = sa.text("''")


def models_to_agents_sql() -> str:
    """One UPDATE: {"reply": {"model": "..."}, ...} with only the non-empty columns
    (`jsonb_strip_nulls` drops the agents whose column was empty)."""
    pairs = ",\n        ".join(
        f"'{agent}', CASE WHEN btrim({column}) <> '' "
        f"THEN jsonb_build_object('model', btrim({column})) END"
        for column, agent in COLUMN_AGENTS.items())
    return f"UPDATE persona SET agents = jsonb_strip_nulls(jsonb_build_object(\n        {pairs}))"


def agents_to_models_sql() -> str:
    sets = ",\n    ".join(
        f"{column} = coalesce(agents -> '{agent}' ->> 'model', '')"
        for column, agent in COLUMN_AGENTS.items())
    return f"UPDATE persona SET\n    {sets}"


MODELS_TO_AGENTS = models_to_agents_sql()
AGENTS_TO_MODELS = agents_to_models_sql()


def upgrade() -> None:
    op.add_column("persona", sa.Column("agents", JSONB(), server_default=JSON_DICT,
                                       nullable=False))
    op.execute(MODELS_TO_AGENTS)
    for column in COLUMN_AGENTS:
        op.drop_column("persona", column)


def downgrade() -> None:
    for column in COLUMN_AGENTS:
        op.add_column("persona", sa.Column(column, sa.String(160), server_default=EMPTY,
                                           nullable=False))
    op.execute(AGENTS_TO_MODELS)
    op.drop_column("persona", "agents")
