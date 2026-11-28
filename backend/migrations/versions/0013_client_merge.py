"""One person in many messengers (docs/leads.md, docs/database.md):

- `client_distinct` — pairs of clients the operator marked «это разные люди», so the
  «возможно, один человек» hint does not offer them again;
- `chat_event.kind` allows `client_merged` (the operator merged another client into this one).

Existing rows are not touched. CHECK names `ck_<table>_<enum class>` as in 0001. Downgrade
deletes the `client_merged` events before the old CHECK returns and drops the table (merged
clients stay merged).

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: tuple[str, ...] = ("client_distinct",)
PREVIOUS_EVENT_KINDS: tuple[str, ...] = (
    "message_in", "message_out", "message_edited", "message_deleted", "media_understood",
    "send_failed", "call", "generation_started", "generation_failed", "guard_blocked",
    "operator_needed", "ai_toggled", "operator_note", "client_updated", "memory_updated",
    "attention", "initiative")

# (table, column) -> (enum name, allowed values) changed by this revision; with 0001-0012 this
# is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("chat_event", "kind"): ("eventkind", (*PREVIOUS_EVENT_KINDS, "client_merged")),
}


def tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_event_check(values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[("chat_event", "kind")]
    constraint = op.f(f"ck_chat_event_{name}")
    op.drop_constraint(constraint, "chat_event", type_="check")
    op.create_check_constraint(constraint, "chat_event", f"kind IN ({allowed(values)})")


def upgrade() -> None:
    _replace_event_check(ENUM_CHECKS[("chat_event", "kind")][1])
    op.execute("CREATE SEQUENCE client_distinct_id_seq")
    op.create_table(
        "client_distinct",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("other_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", tstz(), nullable=False),
        sa.Column("updated_at", tstz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_distinct")),
        sa.ForeignKeyConstraint(["client_id"], ["client.id"],
                                name=op.f("fk_client_distinct_client_id_client"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["other_id"], ["client.id"],
                                name=op.f("fk_client_distinct_other_id_client"),
                                ondelete="CASCADE"),
        sa.UniqueConstraint("client_id", "other_id",
                            name=op.f("uq_client_distinct_client_id")),
    )
    op.create_index(op.f("ix_client_distinct_other_id"), "client_distinct", ["other_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_client_distinct_other_id"), table_name="client_distinct")
    op.drop_table("client_distinct")
    op.execute("DROP SEQUENCE IF EXISTS client_distinct_id_seq")
    op.execute("DELETE FROM chat_event WHERE kind = 'client_merged'")
    _replace_event_check(PREVIOUS_EVENT_KINDS)
