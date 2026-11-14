"""Eva Kogan's birth date: 26 January 1989 (37 now, 38 on 26 January 2027)."""
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVA_NAME = "Ева Коган"
EVA_BIRTH_DATE = date(1989, 1, 26)
EVA_AGE = 37


def upgrade() -> None:
    persona = sa.table(
        "persona", sa.column("name", sa.String), sa.column("birth_date", sa.Date), sa.column("age", sa.Integer)
    )
    op.execute(
        persona.update().where(persona.c.name == EVA_NAME).values(birth_date=EVA_BIRTH_DATE, age=EVA_AGE)
    )


def downgrade() -> None:
    """A data correction: the previous (wrong) values are not restored."""
