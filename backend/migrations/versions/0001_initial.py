"""SIM v2 initial schema: exactly sim/db/models.py (docs/database.md).

Written by hand and frozen. It mirrors what `Base.metadata.create_all()` emits on PostgreSQL:
- `id BIGINT` primary keys fed by a separate sequence `<table>_id_seq` (Advanced Alchemy's
  `BigIntPrimaryKey` uses `Sequence(optional=False)`, so there is no SERIAL / DEFAULT nextval);
- `created_at` / `updated_at` TIMESTAMPTZ filled by the application;
- enums as VARCHAR(32) with a CHECK named `ck_<table>_<enumclass lowercase>`;
- constraint names from Advanced Alchemy's naming convention (pk_, uq_, fk_, ix_);
- `persona.avatar_media_id` -> `media_asset` added after both tables exist (use_alter).

The CI gate (scripts/ci_postgres_smoke.py) compares the migrated database with the models and
the CHECK values with the enums, so any drift fails CI.

Revision ID: 0001
Revises:
Create Date: 2026-11-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("sim",)
depends_on: str | Sequence[str] | None = None

# Creation order respects foreign keys (persona.avatar_media_id is added at the end).
TABLES: tuple[str, ...] = (
    "admin_user", "auth_session", "persona", "media_asset", "channel", "client",
    "client_identity", "chat", "reply_run", "message", "generation", "call", "chat_event",
    "llm_call",
)

# (table, column) -> (enum name, allowed values). Frozen copy of sim/domain/enums.py.
ENUM_CHECKS: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("channel", "kind"): ("channelkind", ("telegram_user",)),
    ("channel", "status"): ("channelstatus", ("new", "connected", "error", "disabled")),
    ("client_identity", "channel_kind"): ("channelkind", ("telegram_user",)),
    ("message", "direction"): ("direction", ("in", "out")),
    ("message", "author"): ("author", ("client", "persona", "operator")),
    ("message", "kind"): ("messagekind", (
        "text", "photo", "video", "voice", "video_note", "file", "sticker")),
    ("message", "status"): ("messagestatus", ("pending", "sent", "failed", "received")),
    ("media_asset", "source"): ("mediasource", ("library", "generated", "incoming")),
    ("media_asset", "kind"): ("mediakind", ("photo", "video", "audio", "file")),
    ("media_asset", "understanding"): ("understandingstatus", (
        "pending", "done", "failed", "skipped")),
    ("reply_run", "status"): ("runstatus", ("running", "sent", "blocked", "skipped", "failed")),
    ("generation", "kind"): ("generationkind", ("photo", "video", "voice")),
    ("generation", "status"): ("generationstatus", ("queued", "running", "done", "failed")),
    ("call", "direction"): ("calldirection", ("in", "out")),
    ("call", "status"): ("callstatus", (
        "ringing", "active", "ended", "missed", "declined", "failed")),
    ("llm_call", "purpose"): ("llmpurpose", (
        "reply", "guard", "vision", "transcribe", "document", "tts", "image", "video", "live",
        "memory")),
    ("chat_event", "kind"): ("eventkind", (
        "message_in", "message_out", "message_edited", "message_deleted", "media_understood",
        "send_failed", "call", "generation_started", "generation_failed", "guard_blocked",
        "operator_needed", "ai_toggled", "operator_note", "client_updated", "memory_updated")),
}

JSON_LIST = sa.text("'[]'::jsonb")
JSON_DICT = sa.text("'{}'::jsonb")


def tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def pk_column() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False)


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", tstz(), nullable=False),
        sa.Column("updated_at", tstz(), nullable=False),
    ]


def enum_column(table: str, column: str) -> sa.Column:
    return sa.Column(column, sa.String(32), nullable=False)


def enum_check(table: str, column: str) -> sa.CheckConstraint:
    name, values = ENUM_CHECKS[(table, column)]
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{name}"))


def fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete)


def pk(table: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}"))


def index(table: str, column: str) -> None:
    op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"CREATE SEQUENCE {table}_id_seq")

    op.create_table(
        "admin_user",
        pk_column(),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *audit_columns(),
        pk("admin_user"),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_admin_user_telegram_id")),
    )

    op.create_table(
        "auth_session",
        pk_column(),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", tstz(), nullable=False),
        *audit_columns(),
        pk("auth_session"),
        fk("auth_session", "admin_id", "admin_user", "CASCADE"),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_session_token_hash")),
    )
    index("auth_session", "admin_id")

    op.create_table(
        "persona",
        pk_column(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("gender", sa.String(32), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("avatar_media_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model_reply", sa.String(160), nullable=False),
        sa.Column("model_guard", sa.String(160), nullable=False),
        sa.Column("model_vision", sa.String(160), nullable=False),
        sa.Column("model_transcribe", sa.String(160), nullable=False),
        sa.Column("model_memory", sa.String(160), nullable=False),
        sa.Column("model_tts", sa.String(160), nullable=False),
        sa.Column("model_image", sa.String(160), nullable=False),
        sa.Column("model_video", sa.String(160), nullable=False),
        sa.Column("model_live", sa.String(160), nullable=False),
        sa.Column("voice_id", sa.String(64), nullable=False),
        sa.Column("voice_style", sa.Text(), nullable=False),
        sa.Column("guard_rules", sa.Text(), nullable=False),
        sa.Column("ai_enabled", sa.Boolean(), nullable=False),
        sa.Column("voice_enabled", sa.Boolean(), nullable=False),
        sa.Column("photo_enabled", sa.Boolean(), nullable=False),
        sa.Column("video_enabled", sa.Boolean(), nullable=False),
        sa.Column("calls_enabled", sa.Boolean(), nullable=False),
        sa.Column("reply_delay_s", sa.Integer(), nullable=False),
        *audit_columns(),
        pk("persona"),
        sa.UniqueConstraint("name", name=op.f("uq_persona_name")),
    )

    op.create_table(
        "media_asset",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=True),
        enum_column("media_asset", "source"),
        enum_column("media_asset", "kind"),
        sa.Column("is_reference", sa.Boolean(), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("tags", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        enum_column("media_asset", "understanding"),
        sa.Column("understanding_error", sa.Text(), nullable=True),
        sa.Column("generation_id", sa.BigInteger(), nullable=True),
        *audit_columns(),
        pk("media_asset"),
        fk("media_asset", "persona_id", "persona", "CASCADE"),
        sa.UniqueConstraint("storage_key", name=op.f("uq_media_asset_storage_key")),
        enum_check("media_asset", "source"),
        enum_check("media_asset", "kind"),
        enum_check("media_asset", "understanding"),
    )
    op.create_index("ix_media_persona_source", "media_asset", ["persona_id", "source", "kind"])
    index("media_asset", "sha256")

    op.create_table(
        "channel",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        enum_column("channel", "kind"),
        sa.Column("title", sa.String(120), nullable=False),
        enum_column("channel", "status"),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("account_label", sa.String(120), nullable=False),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_activity_at", tstz(), nullable=True),
        *audit_columns(),
        pk("channel"),
        fk("channel", "persona_id", "persona", "CASCADE"),
        enum_check("channel", "kind"),
        enum_check("channel", "status"),
    )
    index("channel", "persona_id")

    op.create_table(
        "client",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("ai_enabled", sa.Boolean(), nullable=False),
        sa.Column("memory", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("last_seen_at", tstz(), nullable=True),
        *audit_columns(),
        pk("client"),
        fk("client", "persona_id", "persona", "CASCADE"),
    )
    index("client", "persona_id")

    op.create_table(
        "client_identity",
        pk_column(),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        enum_column("client_identity", "channel_kind"),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("access_hash", sa.String(64), nullable=True),
        *audit_columns(),
        pk("client_identity"),
        fk("client_identity", "client_id", "client", "CASCADE"),
        sa.UniqueConstraint("channel_kind", "external_id",
                            name=op.f("uq_client_identity_channel_kind")),
        enum_check("client_identity", "channel_kind"),
    )
    index("client_identity", "client_id")

    op.create_table(
        "chat",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("ai_enabled", sa.Boolean(), nullable=False),
        sa.Column("needs_operator", sa.Boolean(), nullable=False),
        sa.Column("unread_count", sa.Integer(), nullable=False),
        sa.Column("last_message_at", tstz(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_until_event_id", sa.BigInteger(), nullable=True),
        *audit_columns(),
        pk("chat"),
        fk("chat", "persona_id", "persona", "CASCADE"),
        fk("chat", "client_id", "client", "CASCADE"),
        fk("chat", "channel_id", "channel", "CASCADE"),
        sa.UniqueConstraint("channel_id", "client_id", name=op.f("uq_chat_channel_id")),
    )
    index("chat", "persona_id")
    index("chat", "client_id")
    index("chat", "channel_id")
    index("chat", "last_message_at")

    op.create_table(
        "reply_run",
        pk_column(),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        enum_column("reply_run", "status"),
        sa.Column("trigger_event_id", sa.BigInteger(), nullable=True),
        sa.Column("context", JSONB(), server_default=JSON_DICT, nullable=False),
        sa.Column("plan", JSONB(), server_default=JSON_DICT, nullable=False),
        sa.Column("guard_verdicts", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", tstz(), nullable=True),
        *audit_columns(),
        pk("reply_run"),
        fk("reply_run", "chat_id", "chat", "CASCADE"),
        enum_check("reply_run", "status"),
    )
    op.create_index("ix_reply_run_chat", "reply_run", ["chat_id", "id"])

    op.create_table(
        "message",
        pk_column(),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        enum_column("message", "direction"),
        enum_column("message", "author"),
        enum_column("message", "kind"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("media_id", sa.BigInteger(), nullable=True),
        enum_column("message", "status"),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("reply_to_external_id", sa.String(64), nullable=True),
        sa.Column("reply_run_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", tstz(), nullable=True),
        *audit_columns(),
        pk("message"),
        fk("message", "chat_id", "chat", "CASCADE"),
        fk("message", "media_id", "media_asset", "SET NULL"),
        fk("message", "reply_run_id", "reply_run", "SET NULL"),
        enum_check("message", "direction"),
        enum_check("message", "author"),
        enum_check("message", "kind"),
        enum_check("message", "status"),
    )
    op.create_index("ix_message_chat", "message", ["chat_id", "id"])
    op.create_index("uq_message_external", "message", ["chat_id", "direction", "external_id"],
                    unique=True, postgresql_where=sa.text("external_id IS NOT NULL"))

    op.create_table(
        "generation",
        pk_column(),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("reply_run_id", sa.BigInteger(), nullable=True),
        enum_column("generation", "kind"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("tags", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("ref_media_ids", JSONB(), server_default=JSON_LIST, nullable=False),
        enum_column("generation", "status"),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("result_media_id", sa.BigInteger(), nullable=True),
        sa.Column("broker_job_id", sa.String(80), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", tstz(), nullable=True),
        *audit_columns(),
        pk("generation"),
        fk("generation", "persona_id", "persona", "CASCADE"),
        fk("generation", "chat_id", "chat", "SET NULL"),
        fk("generation", "reply_run_id", "reply_run", "SET NULL"),
        fk("generation", "result_media_id", "media_asset", "SET NULL"),
        enum_check("generation", "kind"),
        enum_check("generation", "status"),
    )
    index("generation", "persona_id")

    op.create_table(
        "call",
        pk_column(),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        enum_column("call", "direction"),
        enum_column("call", "status"),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("started_at", tstz(), nullable=True),
        sa.Column("ended_at", tstz(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("transcript", JSONB(), server_default=JSON_LIST, nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *audit_columns(),
        pk("call"),
        fk("call", "chat_id", "chat", "CASCADE"),
        enum_check("call", "direction"),
        enum_check("call", "status"),
    )
    index("call", "chat_id")

    op.create_table(
        "chat_event",
        pk_column(),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        enum_column("chat_event", "kind"),
        sa.Column("in_context", sa.Boolean(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("media_id", sa.BigInteger(), nullable=True),
        sa.Column("call_id", sa.BigInteger(), nullable=True),
        sa.Column("generation_id", sa.BigInteger(), nullable=True),
        sa.Column("reply_run_id", sa.BigInteger(), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("data", JSONB(), server_default=JSON_DICT, nullable=False),
        *audit_columns(),
        pk("chat_event"),
        fk("chat_event", "chat_id", "chat", "CASCADE"),
        fk("chat_event", "message_id", "message", "SET NULL"),
        fk("chat_event", "media_id", "media_asset", "SET NULL"),
        fk("chat_event", "call_id", "call", "SET NULL"),
        fk("chat_event", "generation_id", "generation", "SET NULL"),
        fk("chat_event", "reply_run_id", "reply_run", "SET NULL"),
        enum_check("chat_event", "kind"),
    )
    op.create_index("ix_chat_event_chat", "chat_event", ["chat_id", "id"])

    op.create_table(
        "llm_call",
        pk_column(),
        sa.Column("reply_run_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        enum_column("llm_call", "purpose"),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("request", JSONB(), server_default=JSON_DICT, nullable=False),
        sa.Column("response", JSONB(), server_default=JSON_DICT, nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *audit_columns(),
        pk("llm_call"),
        fk("llm_call", "reply_run_id", "reply_run", "SET NULL"),
        fk("llm_call", "chat_id", "chat", "SET NULL"),
        enum_check("llm_call", "purpose"),
    )
    op.create_index("ix_llm_call_run", "llm_call", ["reply_run_id"])
    index("llm_call", "chat_id")

    op.create_foreign_key(
        op.f("fk_persona_avatar_media_id_media_asset"), "persona", "media_asset",
        ["avatar_media_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_persona_avatar_media_id_media_asset"), "persona",
                       type_="foreignkey")
    for table in reversed(TABLES):
        op.drop_table(table)
    for table in reversed(TABLES):
        op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")
