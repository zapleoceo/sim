"""The role game is removed: its persona, strategy, settings and columns.

The game persona (`persona.is_game`, seeded as "Sim1") goes the way the admin deletes a persona
(`DELETE /api/personas/{id}`): her gallery rows and simulation branches go with her. A persona
still used by a channel, a client or a content post cannot be deleted there either: she is kept
as an ordinary switched-off persona, for the owner to sort out in the admin. Gallery files stay
on disk: migrations run without the media volume."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GAME_STRATEGY_NAME = "Sim1 — игра"
GAME_SETTING_KEYS = ("game_channel_id", "game_owner_telegram_id")
# Tables whose rows keep a persona from being deleted / go away together with her.
USED_BY = ("channel", "client", "content_post")
OWNED = ("persona_media", "sim_branch")

persona = sa.table(
    "persona", sa.column("id", sa.Integer), sa.column("is_game", sa.Boolean),
    sa.column("is_active", sa.Boolean), sa.column("strategy_id", sa.Integer),
)
strategy = sa.table("strategy", sa.column("id", sa.Integer), sa.column("name", sa.String))
app_setting = sa.table("app_setting", sa.column("key", sa.String))
sim_branch = sa.table("sim_branch", sa.column("id", sa.Integer), sa.column("persona_id", sa.Integer))
sim_message = sa.table("sim_message", sa.column("branch_id", sa.Integer))


def by_persona(name: str) -> sa.TableClause:
    return sa.table(name, sa.column("persona_id", sa.Integer))


def remove_game_persona(bind: sa.Connection, persona_id: int) -> None:
    for name in USED_BY:
        table = by_persona(name)
        if bind.scalar(sa.select(sa.func.count()).where(table.c.persona_id == persona_id)):
            bind.execute(persona.update().where(persona.c.id == persona_id).values(is_active=False))
            return
    branches = sa.select(sim_branch.c.id).where(sim_branch.c.persona_id == persona_id)
    bind.execute(sim_message.delete().where(sim_message.c.branch_id.in_(branches)))
    for name in OWNED:
        table = by_persona(name)
        bind.execute(table.delete().where(table.c.persona_id == persona_id))
    bind.execute(persona.delete().where(persona.c.id == persona_id))


def upgrade() -> None:
    bind = op.get_bind()
    for persona_id in bind.scalars(sa.select(persona.c.id).where(persona.c.is_game.is_(True))).all():
        remove_game_persona(bind, persona_id)
    in_use = sa.select(persona.c.strategy_id).where(persona.c.strategy_id.is_not(None))
    bind.execute(strategy.delete().where(
        strategy.c.name == GAME_STRATEGY_NAME, strategy.c.id.not_in(in_use)))
    bind.execute(app_setting.delete().where(app_setting.c.key.in_(GAME_SETTING_KEYS)))
    with op.batch_alter_table("dialog") as batch:
        batch.drop_column("game_consent_at")
        batch.drop_column("game_state")
    with op.batch_alter_table("persona") as batch:
        batch.drop_column("is_game")


def downgrade() -> None:
    """The columns come back empty; the deleted persona, strategy and settings are not restored."""
    with op.batch_alter_table("persona") as batch:
        batch.add_column(sa.Column("is_game", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("dialog") as batch:
        batch.add_column(sa.Column("game_state", sa.String(length=8), nullable=False,
                                   server_default=""))
        batch.add_column(sa.Column("game_consent_at", sa.DateTime(), nullable=True))
