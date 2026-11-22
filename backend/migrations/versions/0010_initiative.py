"""She writes and calls first (docs/initiative.md, docs/database.md):

- `planned_contact` — a message or a call at a given time about a given topic, planned by the
  manager in the dashboard or by her in a reply turn;
- `chat.calls_out` — the chat's «Звонит сама» switch (off by default);
- `persona.voice_speed` — how fast she speaks in voice notes and calls (1.0 = the model's pace);
- call recordings: `persona.call_recording` (on), `persona.call_recording_days` (90),
  `call.recording_key` and `call.recording_s`;
- `chat_event.kind` allows `initiative` (a planned message is due: she writes first).

CHECK names `ck_<table>_<enum class>` as in 0001. Downgrade deletes the `initiative` events
before the old CHECK returns and drops the table and the columns.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: tuple[str, ...] = ("planned_contact",)
PREVIOUS_EVENT_KINDS: tuple[str, ...] = (
    "message_in", "message_out", "message_edited", "message_deleted", "media_understood",
    "send_failed", "call", "generation_started", "generation_failed", "guard_blocked",
    "operator_needed", "ai_toggled", "operator_note", "client_updated", "memory_updated",
    "attention")

# (table, column) -> (enum name, allowed values) added or changed by this revision; with
# 0001-0009 this is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("chat_event", "kind"): ("eventkind", (*PREVIOUS_EVENT_KINDS, "initiative")),
    ("planned_contact", "kind"): ("contactkind", ("message", "call")),
    ("planned_contact", "source"): ("contactsource", ("manager", "persona")),
    ("planned_contact", "status"): ("contactstatus", (
        "planned", "started", "done", "failed", "cancelled")),
}

EMPTY = sa.text("''")


def tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def enum_check(table: str, column: str) -> sa.CheckConstraint:
    name, values = ENUM_CHECKS[(table, column)]
    return sa.CheckConstraint(f"{column} IN ({allowed(values)})", name=op.f(f"ck_{table}_{name}"))


def _replace_event_check(values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[("chat_event", "kind")]
    constraint = op.f(f"ck_chat_event_{name}")
    op.drop_constraint(constraint, "chat_event", type_="check")
    op.create_check_constraint(constraint, "chat_event", f"kind IN ({allowed(values)})")


def upgrade() -> None:
    op.add_column("chat", sa.Column("calls_out", sa.Boolean(),
                                    server_default=sa.text("false"), nullable=False))
    op.add_column("persona", sa.Column("voice_speed", sa.Float(),
                                       server_default=sa.text("1.0"), nullable=False))
    op.add_column("persona", sa.Column("call_recording", sa.Boolean(),
                                       server_default=sa.text("true"), nullable=False))
    op.add_column("persona", sa.Column("call_recording_days", sa.Integer(),
                                       server_default=sa.text("90"), nullable=False))
    op.add_column("call", sa.Column("recording_key", sa.String(255), nullable=True))
    op.add_column("call", sa.Column("recording_s", sa.Float(), nullable=True))
    _replace_event_check(ENUM_CHECKS[("chat_event", "kind")][1])
    op.execute("CREATE SEQUENCE planned_contact_id_seq")
    op.create_table(
        "planned_contact",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("due_at", tstz(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("finished_at", tstz(), nullable=True),
        sa.Column("call_id", sa.BigInteger(), nullable=True),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", tstz(), nullable=False),
        sa.Column("updated_at", tstz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planned_contact")),
        sa.ForeignKeyConstraint(["chat_id"], ["chat.id"],
                                name=op.f("fk_planned_contact_chat_id_chat"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["call_id"], ["call.id"],
                                name=op.f("fk_planned_contact_call_id_call"), ondelete="SET NULL"),
        enum_check("planned_contact", "kind"),
        enum_check("planned_contact", "source"),
        enum_check("planned_contact", "status"),
    )
    op.create_index(op.f("ix_planned_contact_chat_id"), "planned_contact", ["chat_id"])
    op.create_index("ix_planned_contact_due", "planned_contact", ["status", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_planned_contact_due", table_name="planned_contact")
    op.drop_index(op.f("ix_planned_contact_chat_id"), table_name="planned_contact")
    op.drop_table("planned_contact")
    op.execute("DROP SEQUENCE IF EXISTS planned_contact_id_seq")
    op.execute("DELETE FROM chat_event WHERE kind = 'initiative'")
    _replace_event_check(PREVIOUS_EVENT_KINDS)
    op.drop_column("call", "recording_s")
    op.drop_column("call", "recording_key")
    op.drop_column("persona", "call_recording_days")
    op.drop_column("persona", "call_recording")
    op.drop_column("persona", "voice_speed")
    op.drop_column("chat", "calls_out")
