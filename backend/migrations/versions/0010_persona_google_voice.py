"""Persona voice via Google Gemini TTS: voice_mode + voice_style.

`voice_id` now holds a Gemini prebuilt voice name. Its old content was an ElevenLabs voice id
that nothing ever used (outgoing voice did not exist); such values are cleared so the admin
form does not carry a value it cannot show."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of sim.domain.voice.GEMINI_VOICES at the time of this migration.
GEMINI_VOICES = (
    "Kore", "Aoede", "Leda", "Zephyr", "Callirrhoe", "Autonoe", "Despina", "Erinome",
    "Laomedeia", "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat",
    "Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Algenib", "Rasalgethi", "Alnilam", "Schedar", "Achird", "Zubenelgenubi",
    "Sadachbia", "Sadaltager",
)


def upgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("voice_mode", sa.String(32), nullable=False,
                                   server_default="off"))
        batch.add_column(sa.Column("voice_style", sa.Text(), nullable=False,
                                   server_default=""))
    persona = sa.table("persona", sa.column("voice_id", sa.String))
    op.execute(
        persona.update()
        .where(sa.and_(persona.c.voice_id != "", persona.c.voice_id.notin_(GEMINI_VOICES)))
        .values(voice_id="")
    )


def downgrade() -> None:
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("voice_style")
        batch.drop_column("voice_mode")
