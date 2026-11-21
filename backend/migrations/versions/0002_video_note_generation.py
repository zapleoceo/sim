"""Round video messages («кружок»): `generation.kind` allows `video_note`, and `generation.speech`
keeps the exact words she says on camera (docs/database.md, docs/reply.md).

The CHECK keeps the 0001 name `ck_generation_generationkind`, so the CI gate
(scripts/ci_postgres_smoke.py) compares it with GenerationKind as before. Downgrade turns
video-note generations into plain video ones (rows are kept) and drops `speech`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) -> (enum name, allowed values) changed by this revision; together with
# 0001's ENUM_CHECKS this is the frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("generation", "kind"): ("generationkind", ("photo", "video", "voice", "video_note")),
}
PREVIOUS_KINDS: tuple[str, ...] = ("photo", "video", "voice")


def _replace_kind_check(values: Sequence[str]) -> None:
    name, _ = ENUM_CHECKS[("generation", "kind")]
    constraint = op.f(f"ck_generation_{name}")
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint(constraint, "generation", type_="check")
    op.create_check_constraint(constraint, "generation", f"kind IN ({allowed})")


def upgrade() -> None:
    _replace_kind_check(ENUM_CHECKS[("generation", "kind")][1])
    op.add_column("generation", sa.Column("speech", sa.Text(), server_default=sa.text("''"),
                                          nullable=False))


def downgrade() -> None:
    op.drop_column("generation", "speech")
    op.execute("UPDATE generation SET kind = 'video' WHERE kind = 'video_note'")
    _replace_kind_check(PREVIOUS_KINDS)
