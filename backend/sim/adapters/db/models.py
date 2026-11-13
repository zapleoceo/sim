"""ORM models: one class per table in docs/architecture.md (plus auth_session and the
conversation bookkeeping columns). Datetimes are naive UTC."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sim.adapters.db.base import Base, enum_col
from sim.clock import utcnow
from sim.domain.enums import (
    Author,
    ChannelKind,
    ClientStatus,
    MediaKind,
    ContentKind,
    ContentStatus,
    Delivery,
    Direction,
    PartnerEventKind,
    Role,
    SessionStatus,
    Stage,
    VoiceAmbience,
    VoiceMode,
)

Money = Numeric(14, 2, asdecimal=False)


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Admins sign in only through Telegram. telegram_id is NULL until an admin invited by
    # @username logs in for the first time (then it is bound).
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    tg_username: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # lowercase, no @
    display_name: Mapped[str] = mapped_column(String(120), default="")
    photo_url: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[Role] = mapped_column(enum_col(Role), default=Role.OPERATOR)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    """Server-side session. Only sha256(token) is stored; the raw token lives in the cookie."""

    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[AdminUser] = relationship(lazy="joined")


class UsedLoginHash(Base):
    """Telegram widget `hash` values already accepted (one-time use). Purged after expiry."""

    __tablename__ = "used_login_hash"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class LoginLink(Base):
    """Break-glass one-time login URL issued by `python -m sim.cli login-link`. Only sha256."""

    __tablename__ = "login_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Strategy(Base):
    __tablename__ = "strategy"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    # Free-text conversation guidance (guidelines, not a script), rendered in the stable system prompt.
    prompt: Mapped[str] = mapped_column(Text, default="")
    offer: Mapped[str] = mapped_column(Text, default="")
    partner_name: Mapped[str] = mapped_column(String(120), default="")
    registration_url: Mapped[str] = mapped_column(Text, default="")
    stage_playbook: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    followup_hours: Mapped[list[float]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Persona(Base):
    __tablename__ = "persona"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Exact birthday and home time zone: age, zodiac and local time are computed by code
    # (sim.services.persona_clock) and given to the model, never left to its arithmetic.
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Kyiv", server_default="Europe/Kyiv")
    gender: Mapped[str] = mapped_column(String(32), default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    # Free text: who she is, character, biography, how she writes (Markdown-ish).
    description: Mapped[str] = mapped_column(Text, default="")
    # What she must never write or do: one rule per line.
    prohibitions: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(Text, default="ru")
    model: Mapped[str] = mapped_column(String(120), default="")
    # Google Gemini TTS prebuilt voice name (sim.domain.voice.GEMINI_VOICES), "" = no voice.
    voice_id: Mapped[str] = mapped_column(String(120), default="")
    voice_mode: Mapped[VoiceMode] = mapped_column(enum_col(VoiceMode), default=VoiceMode.OFF)
    # Delivery hint read by the TTS model before the text, e.g. "тепло, спокойно, с улыбкой".
    voice_style: Mapped[str] = mapped_column(Text, default="")
    # "Recorded on a phone" in a room / on the street / in a cafe (sim.adapters.ambience), or clean.
    voice_ambience: Mapped[VoiceAmbience] = mapped_column(
        enum_col(VoiceAmbience), default=VoiceAmbience.OFF
    )
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PersonaMedia(Base):
    __tablename__ = "persona_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    kind: Mapped[MediaKind] = mapped_column(enum_col(MediaKind))
    storage_name: Mapped[str] = mapped_column(String(120))
    caption: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Channel(Base):
    __tablename__ = "channel"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ChannelKind] = mapped_column(enum_col(ChannelKind))
    name: Mapped[str] = mapped_column(String(120), default="")
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id"))
    status: Mapped[SessionStatus] = mapped_column(
        enum_col(SessionStatus), default=SessionStatus.NEW
    )
    account_label: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    session_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet token
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Client(Base):
    __tablename__ = "client"

    id: Mapped[int] = mapped_column(primary_key=True)
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id"), index=True)
    display_name: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    handle: Mapped[str] = mapped_column(String(120), default="")
    ref_code: Mapped[str] = mapped_column(String(32), unique=True)
    stage: Mapped[Stage] = mapped_column(enum_col(Stage), default=Stage.FIRST_CONTACT, index=True)
    status: Mapped[ClientStatus] = mapped_column(
        enum_col(ClientStatus), default=ClientStatus.ACTIVE
    )
    next_step: Mapped[str] = mapped_column(Text, default="")
    next_step_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # The lead passport kept by the passportist agent (sim.services.passport).
    passport: Mapped[str] = mapped_column(Text, default="")
    passport_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    needs: Mapped[list[str]] = mapped_column(JSON, default=list)
    no_followups: Mapped[bool] = mapped_column(Boolean, default=False)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    identities: Mapped[list[ClientIdentity]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class ClientIdentity(Base):
    """One person's address in one channel. Scoped by channel (not just kind) so the same
    Telegram user talking to two persona bots is two clients."""

    __tablename__ = "client_identity"
    __table_args__ = (UniqueConstraint("channel_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id"))
    channel_kind: Mapped[ChannelKind] = mapped_column(enum_col(ChannelKind))
    external_id: Mapped[str] = mapped_column(String(200))

    client: Mapped[Client] = relationship(back_populates="identities")


class Dialog(Base):
    __tablename__ = "dialog"
    __table_args__ = (UniqueConstraint("channel_id", "external_thread_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id"), index=True)
    external_thread_id: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    ai_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True
    )
    unread: Mapped[int] = mapped_column(Integer, default=0)
    followup_step: Mapped[int] = mapped_column(Integer, default=0)
    last_message_text: Mapped[str] = mapped_column(Text, default="")
    last_message_direction: Mapped[Direction | None] = mapped_column(
        enum_col(Direction), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    channel: Mapped[Channel] = relationship(lazy="joined")
    client: Mapped[Client] = relationship(lazy="joined")


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        UniqueConstraint("dialog_id", "external_id"),
        UniqueConstraint("dialog_id", "client_request_id"),
        Index("ix_message_dialog_id_id", "dialog_id", "id"),
        Index("ix_message_created_at_id", "created_at", "id"),
        Index("ix_message_dialog_created_at_id", "dialog_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dialog_id: Mapped[int] = mapped_column(ForeignKey("dialog.id", ondelete="CASCADE"))
    direction: Mapped[Direction] = mapped_column(enum_col(Direction))
    author: Mapped[Author] = mapped_column(enum_col(Author))
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True,
    )
    operator_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    media_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery: Mapped[Delivery] = mapped_column(enum_col(Delivery), default=Delivery.SENT)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Pre-send guard (AI messages only): ok | rewritten | blocked.
    guard_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    guard_violations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    guard_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # generator draft if rewritten
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class OutboundSendIntent(Base):
    """Permanent send fence, without FK/cascade; sent_at marks an acknowledged send."""

    __tablename__ = "outbound_send_intent"

    message_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FunnelEvent(Base):
    __tablename__ = "funnel_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "stage" | "status"
    from_value: Mapped[str] = mapped_column(String(32))
    to_value: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PartnerEvent(Base):
    __tablename__ = "partner_event"
    __table_args__ = (UniqueConstraint("kind", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id", ondelete="CASCADE"), index=True)
    kind: Mapped[PartnerEventKind] = mapped_column(enum_col(PartnerEventKind))
    external_id: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float | None] = mapped_column(Money, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="")
    payout: Mapped[float | None] = mapped_column(Money, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="webhook")  # webhook | csv
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContentPost(Base):
    __tablename__ = "content_post"

    id: Mapped[int] = mapped_column(primary_key=True)
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id"))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channel.id"), nullable=True)
    kind: Mapped[ContentKind] = mapped_column(enum_col(ContentKind), default=ContentKind.PHOTO)
    caption: Mapped[str] = mapped_column(Text, default="")
    media_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[ContentStatus] = mapped_column(
        enum_col(ContentStatus), default=ContentStatus.DRAFT
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContentPublishIntent(Base):
    """Permanent upload fence. Deliberately no FK: independent commits must not lock posts."""

    __tablename__ = "content_publish_intent"

    post_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Job(Base):
    """Work queue. status: pending | running | done | failed | cancelled."""

    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status_run_at", "status", "run_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # The trace (trace_event.trace_id) that enqueued this job; the worker continues it.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LlmCall(Base):
    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(primary_key=True)
    dialog_id: Mapped[int | None] = mapped_column(
        ForeignKey("dialog.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow: Mapped[str] = mapped_column(String(32), default="")
    capability: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class TraceEvent(Base):
    """System log: one step of one traced request (HTTP call, webhook, inbound message, job
    attempt, LLM/guard call, send...). Link columns are plain ids without FKs: the log outlives
    deleted rows and never locks them. Purged after `trace_retention_days`."""

    __tablename__ = "trace_event"
    __table_args__ = (
        Index("ix_trace_event_started_at_id", "started_at", "id"),
        Index("ix_trace_event_trace_started", "trace_id", "started_at"),
        Index("ix_trace_event_kind_started", "kind", "started_at"),
        Index("ix_trace_event_status_started", "status", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    dialog_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_call_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SimBranch(Base):
    """An admin's test conversation with a persona inside the login bot. Never a client/dialog:
    simulations stay out of the inbox and analytics and are never sent to a real channel."""

    __tablename__ = "sim_branch"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_user.id", ondelete="CASCADE"), index=True
    )
    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Simulated funnel state, kept in the branch instead of a client row.
    stage: Mapped[Stage] = mapped_column(enum_col(Stage), default=Stage.FIRST_CONTACT)
    status: Mapped[ClientStatus] = mapped_column(enum_col(ClientStatus), default=ClientStatus.ACTIVE)
    needs: Mapped[list[str]] = mapped_column(JSON, default=list)
    next_step: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    admin: Mapped[AdminUser] = relationship(lazy="joined")
    persona: Mapped[Persona] = relationship(lazy="joined")


class SimMessage(Base):
    __tablename__ = "sim_message"
    __table_args__ = (
        Index("ix_sim_message_branch_id_id", "branch_id", "id"),
        UniqueConstraint("branch_id", "client_request_id", name="uq_sim_message_branch_request"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("sim_branch.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(8))  # client | ai | system
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    guard_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    guard_violations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # generator draft if rewritten
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """An owner-edited value that overrides a code default (prompt texts, tunables). Only keys
    from `sim.services.app_settings.SPECS` are accepted; a missing row means "use the default"."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True
    )


class SimAdminPref(Base):
    """Per-admin simulation settings (the footer toggle `/debug`)."""

    __tablename__ = "sim_admin_pref"

    admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_user.id", ondelete="CASCADE"), primary_key=True
    )
    debug: Mapped[bool] = mapped_column(Boolean, default=True)
