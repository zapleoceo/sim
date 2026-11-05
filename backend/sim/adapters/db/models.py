"""ORM models: one class per table in docs/architecture.md (plus auth_session and the
conversation bookkeeping columns). Datetimes are naive UTC."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    ContentKind,
    ContentStatus,
    Delivery,
    Direction,
    PartnerEventKind,
    Role,
    SessionStatus,
    Stage,
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
    offer: Mapped[str] = mapped_column(Text, default="")
    partner_name: Mapped[str] = mapped_column(String(120), default="")
    registration_url: Mapped[str] = mapped_column(Text, default="")
    stage_playbook: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    followup_hours: Mapped[list[float]] = mapped_column(JSON, default=list)
    reply_delay_s: Mapped[list[float]] = mapped_column(JSON, default=lambda: [20, 90])
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Persona(Base):
    __tablename__ = "persona"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str] = mapped_column(String(32), default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    tagline: Mapped[str] = mapped_column(Text, default="")
    character: Mapped[str] = mapped_column(Text, default="")
    backstory: Mapped[str] = mapped_column(Text, default="")
    communication_style: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(Text, default="ru")
    model: Mapped[str] = mapped_column(String(120), default="")
    voice_id: Mapped[str] = mapped_column(String(120), default="")
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_virtual_disclosed: Mapped[bool] = mapped_column(Boolean, default=True)
    disclosure_text: Mapped[str] = mapped_column(Text, default="")
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
        Index("ix_message_dialog_id_id", "dialog_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dialog_id: Mapped[int] = mapped_column(ForeignKey("dialog.id", ondelete="CASCADE"))
    direction: Mapped[Direction] = mapped_column(enum_col(Direction))
    author: Mapped[Author] = mapped_column(enum_col(Author))
    text: Mapped[str] = mapped_column(Text, default="")
    media_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delivery: Mapped[Delivery] = mapped_column(enum_col(Delivery), default=Delivery.SENT)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
