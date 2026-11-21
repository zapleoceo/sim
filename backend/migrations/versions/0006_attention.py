"""The persona's attention, «Внимание» (docs/attention.md, docs/database.md):
- `persona.attention` (JSONB, the owner's settings: mode, pace, three switches; `{}` = defaults)
  and `persona.attention_check_at` (when her one pending phone check is due);
- `chat.attention_instant` — the chat's «Отвечать сразу» switch;
- `chat_event.kind` allows `attention` (what she did with a chat at a phone check);
- `llm_call.purpose` allows `attention` (the attention agent's calls).

Mirrors `Base.metadata.create_all()` like 0001-0005: CHECK names `ck_<table>_<enum class>`,
server defaults equal to the models'. Downgrade deletes the `attention` rows of `chat_event`
and `llm_call` before the old CHECKs return and drops the new columns.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) -> (enum name, allowed values) changed by this revision; with 0001-0005 this
# is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("llm_call", "purpose"): ("llmpurpose", (
        "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
        "memory", "life", "passport", "attention")),
    ("chat_event", "kind"): ("eventkind", (
        "message_in", "message_out", "message_edited", "message_deleted", "media_understood",
        "send_failed", "call", "generation_started", "generation_failed", "guard_blocked",
        "operator_needed", "ai_toggled", "operator_note", "client_updated", "memory_updated",
        "attention")),
}
PREVIOUS_PURPOSES: tuple[str, ...] = (
    "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
    "memory", "life", "passport")
PREVIOUS_KINDS: tuple[str, ...] = (
    "message_in", "message_out", "message_edited", "message_deleted", "media_understood",
    "send_failed", "call", "generation_started", "generation_failed", "guard_blocked",
    "operator_needed", "ai_toggled", "operator_note", "client_updated", "memory_updated")

JSON_DICT = sa.text("'{}'::jsonb")


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_check(table: str, column: str, values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[(table, column)]
    constraint = op.f(f"ck_{table}_{name}")
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, f"{column} IN ({allowed(values)})")


def upgrade() -> None:
    op.add_column("persona", sa.Column("attention", JSONB(), server_default=JSON_DICT,
                                       nullable=False))
    op.add_column("persona", sa.Column("attention_check_at", sa.DateTime(timezone=True),
                                       nullable=True))
    op.add_column("chat", sa.Column("attention_instant", sa.Boolean(),
                                    server_default=sa.text("false"), nullable=False))
    _replace_check("chat_event", "kind", ENUM_CHECKS[("chat_event", "kind")][1])
    _replace_check("llm_call", "purpose", ENUM_CHECKS[("llm_call", "purpose")][1])


def downgrade() -> None:
    op.execute("DELETE FROM chat_event WHERE kind = 'attention'")
    op.execute("DELETE FROM llm_call WHERE purpose = 'attention'")
    _replace_check("chat_event", "kind", PREVIOUS_KINDS)
    _replace_check("llm_call", "purpose", PREVIOUS_PURPOSES)
    op.drop_column("chat", "attention_instant")
    op.drop_column("persona", "attention_check_at")
    op.drop_column("persona", "attention")
