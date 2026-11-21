"""Instagram Direct and WhatsApp channels (docs/channels.md, docs/database.md):

- `channel.kind` and `client_identity.channel_kind` allow `instagram` and `whatsapp`;
- `channel.status` allows `pending` (a login in progress: QR shown, a code awaited) and
  `challenge` (the messenger wants the owner to log in again);
- `chat.external_thread_id` — the conversation's address where a messenger addresses threads,
  not people (Instagram Direct thread id, WhatsApp jid); null for Telegram.

CHECK names `ck_<table>_<enum class>` as in 0001. Downgrade removes the Instagram / WhatsApp
channels (their chats and messages go with them by the foreign keys) and identities, turns
`pending` / `challenge` channels into `error`, restores the old CHECKs and drops the column.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS: tuple[str, ...] = ("telegram_user", "instagram", "whatsapp")
STATUSES: tuple[str, ...] = ("new", "connected", "error", "disabled", "pending", "challenge")
PREVIOUS_KINDS: tuple[str, ...] = ("telegram_user",)
PREVIOUS_STATUSES: tuple[str, ...] = ("new", "connected", "error", "disabled")

# (table, column) -> (enum name, allowed values) changed by this revision; with 0001-0008 this
# is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("channel", "kind"): ("channelkind", KINDS),
    ("channel", "status"): ("channelstatus", STATUSES),
    ("client_identity", "channel_kind"): ("channelkind", KINDS),
}


def allowed(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_check(table: str, column: str, values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[(table, column)]
    constraint = op.f(f"ck_{table}_{name}")
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, f"{column} IN ({allowed(values)})")


def upgrade() -> None:
    op.add_column("chat", sa.Column("external_thread_id", sa.String(length=128),
                                    nullable=True))
    _replace_check("channel", "kind", KINDS)
    _replace_check("client_identity", "channel_kind", KINDS)
    _replace_check("channel", "status", STATUSES)


def downgrade() -> None:
    new_kinds = allowed(sorted(set(KINDS) - set(PREVIOUS_KINDS)))
    op.execute(f"DELETE FROM channel WHERE kind IN ({new_kinds})")
    op.execute(f"DELETE FROM client_identity WHERE channel_kind IN ({new_kinds})")
    new_statuses = allowed(sorted(set(STATUSES) - set(PREVIOUS_STATUSES)))
    op.execute(f"UPDATE channel SET status = 'error' WHERE status IN ({new_statuses})")
    _replace_check("channel", "status", PREVIOUS_STATUSES)
    _replace_check("client_identity", "channel_kind", PREVIOUS_KINDS)
    _replace_check("channel", "kind", PREVIOUS_KINDS)
    op.drop_column("chat", "external_thread_id")
