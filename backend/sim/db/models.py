"""Database schema of SIM v2 (PostgreSQL only). Described in docs/database.md.

Every table has `id`, `created_at`, `updated_at` from Advanced Alchemy's `BigIntAuditBase`.
Enums are stored as plain strings with a CHECK constraint (`native_enum=False`), so adding a
value is a code change plus a small migration, never an ALTER TYPE."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from advanced_alchemy.base import BigIntAuditBase
from advanced_alchemy.types import DateTimeUTC
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sim.domain.enums import (
    ArcStatus,
    Author,
    CallDirection,
    CallStatus,
    CanonStatus,
    ChannelKind,
    ChannelStatus,
    DaySource,
    Direction,
    EventKind,
    GenerationKind,
    GenerationStatus,
    LlmPurpose,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    RunStatus,
    UnderstandingStatus,
)

Base = BigIntAuditBase


def enum_col(enum: type[StrEnum], **kw: Any) -> Any:
    """A string column limited to the enum's values (CHECK constraint, no native PG enum)."""
    return mapped_column(
        Enum(enum, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e],
             validate_strings=True, create_constraint=True),
        **kw,
    )


def jsonb_list() -> Any:
    return mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


def jsonb_dict() -> Any:
    return mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


# --- access ----------------------------------------------------------------------------------


class AdminUser(Base):
    """Who may open the dashboard. Login is Telegram-only (Login Widget)."""

    __tablename__ = "admin_user"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSession(Base):
    __tablename__ = "auth_session"

    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"),
                                          index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)   # sha256 of the cookie
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTimeUTC)


# --- persona ---------------------------------------------------------------------------------


class Persona(Base):
    """An AI character. There may be many (docs/personas.md); every owned row carries
    `persona_id`, and a chat is always answered by the persona of its channel."""

    __tablename__ = "persona"

    name: Mapped[str] = mapped_column(String(120), unique=True)
    gender: Mapped[str] = mapped_column(String(32), default="female")
    language: Mapped[str] = mapped_column(String(16), default="ru")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Kyiv")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    avatar_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL", use_alter=True), nullable=True)
    # The owner's single prompt: who she is, character, style, prohibitions.
    prompt: Mapped[str] = mapped_column(Text, default="")
    voice_id: Mapped[str] = mapped_column(String(64), default="")       # Gemini prebuilt voice
    voice_style: Mapped[str] = mapped_column(Text, default="")          # TTS delivery hint
    # Short list of what the guard («Охранник») must never let through, one rule per line.
    guard_rules: Mapped[str] = mapped_column(Text, default="")
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    photo_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    video_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    calls_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Seconds to wait for a burst of client messages before answering.
    reply_delay_s: Mapped[int] = mapped_column(Integer, default=8)
    # Her static world: routine, people, hobbies, places (sim/domain/life.PersonaLife).
    life: Mapped[dict[str, Any]] = jsonb_dict()
    # Exact look in English for image models: eyes, hair, face, skin, build, make-up.
    appearance: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    # Voice notes get the background sound of her current scene.
    voice_ambience_enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                                         server_default=text("true"))
    # Her internal agents (docs/agents.md): {agent_key: {model, key_tier, prompt, max_tokens}},
    # only what the owner changed; read through sim.domain.agents.agent_config.
    agents: Mapped[dict[str, Any]] = jsonb_dict()
    # Archived (False): never answers, plans no days, its Telegram accounts go offline.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class Channel(Base):
    """A messenger account the persona speaks through."""

    __tablename__ = "channel"

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"),
                                            index=True)
    kind: Mapped[ChannelKind] = enum_col(ChannelKind)
    title: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[ChannelStatus] = enum_col(ChannelStatus, default=ChannelStatus.NEW)
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # own TG user id
    account_label: Mapped[str] = mapped_column(String(120), default="")         # @username
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)    # Fernet token
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)


# --- the persona's life (docs/persona-life.md) -----------------------------------------------


class PersonaArc(Base):
    """A slow story of her life (a bathroom renovation, a daughter's exhibition)."""

    __tablename__ = "persona_arc"

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"),
                                            index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(Text, default="")        # where the story is now
    status: Mapped[ArcStatus] = enum_col(ArcStatus, default=ArcStatus.ACTIVE)
    started_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class PersonaDay(Base):
    """One planned day of her life, in her local calendar. Planned once, never re-planned by
    itself; the owner's edits mark it `edited`."""

    __tablename__ = "persona_day"
    __table_args__ = (UniqueConstraint("persona_id", "day"),)

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    # [DayEvent] (sim/domain/life.py), in time order.
    events: Mapped[list[dict[str, Any]]] = jsonb_list()
    summary: Mapped[str] = mapped_column(Text, default="")      # how the day went
    source: Mapped[DaySource] = enum_col(DaySource, default=DaySource.MODEL)
    model: Mapped[str] = mapped_column(String(160), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PersonaCanon(Base):
    """A durable fact she invented about herself in a chat; shown in every chat."""

    __tablename__ = "persona_canon"

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"),
                                            index=True)
    fact: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("chat.id", ondelete="SET NULL"),
                                                nullable=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[CanonStatus] = enum_col(CanonStatus, default=CanonStatus.ACTIVE)


# --- people and chats ------------------------------------------------------------------------


class Client(Base):
    """A person the persona talks to (a lead). `passport` is what the agent knows about them
    (docs/leads.md, docs/context.md). The client belongs to the persona of the channel they
    first wrote to; every chat is answered by the persona of its own channel."""

    __tablename__ = "client"

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"),
                                            index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")            # operator's notes, agent sees
    # Off by default: a new client is stored but the persona does not answer until enabled.
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # The living profile: {text, name, age, gender, city, country, timezone, occupation, stage,
    # temperature, next_step, updated_at, locked_fields} (sim/domain/passport.Passport). Written
    # by the passport task and the operator; fields the operator edited are locked for the model.
    passport: Mapped[dict[str, Any]] = jsonb_dict()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)

    identities: Mapped[list[ClientIdentity]] = relationship(
        back_populates="client", cascade="all, delete-orphan", lazy="selectin")


class ClientIdentity(Base):
    """The client's id in one messenger. The same person in two messengers = one client."""

    __tablename__ = "client_identity"
    __table_args__ = (UniqueConstraint("channel_kind", "external_id"),)

    client_id: Mapped[int] = mapped_column(ForeignKey("client.id", ondelete="CASCADE"),
                                           index=True)
    channel_kind: Mapped[ChannelKind] = enum_col(ChannelKind)
    external_id: Mapped[str] = mapped_column(String(64))
    # Telegram needs the access hash to write first / call; kept per identity.
    access_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    client: Mapped[Client] = relationship(back_populates="identities")


class Chat(Base):
    __tablename__ = "chat"
    __table_args__ = (UniqueConstraint("channel_id", "client_id"),)

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"),
                                            index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id", ondelete="CASCADE"),
                                           index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"),
                                            index=True)
    # Telegram's access hash of the client as THIS chat's account sees it (it differs between
    # accounts, so with many personas it lives on the chat, not on the identity).
    peer_access_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True,
                                                             index=True)
    # Rolling summary of events older than `summary_until_event_id` (docs/context.md).
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_until_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Message(Base):
    """A deliverable message in either direction. Its life in the chat is told by `chat_event`."""

    __tablename__ = "message"
    __table_args__ = (
        Index("ix_message_chat", "chat_id", "id"),
        Index("uq_message_external", "chat_id", "direction", "external_id", unique=True,
              postgresql_where=text("external_id IS NOT NULL")),
    )

    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    direction: Mapped[Direction] = enum_col(Direction)
    author: Mapped[Author] = enum_col(Author)
    kind: Mapped[MessageKind] = enum_col(MessageKind)
    text: Mapped[str] = mapped_column(Text, default="")             # text or media caption
    media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[MessageStatus] = enum_col(MessageStatus)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_to_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("reply_run.id", ondelete="SET NULL"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)


class MediaAsset(Base):
    """Every file: the persona's library, what models generated, what clients sent."""

    __tablename__ = "media_asset"
    __table_args__ = (Index("ix_media_persona_source", "persona_id", "source", "kind"),)

    persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), nullable=True)
    source: Mapped[MediaSource] = enum_col(MediaSource)
    kind: Mapped[MediaKind] = enum_col(MediaKind)
    # Library photo of the persona's own appearance: sent to generation as a face/body reference.
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    mime: Mapped[str] = mapped_column(String(100))
    file_name: Mapped[str] = mapped_column(String(255), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    caption: Mapped[str] = mapped_column(Text, default="")          # what it shows, one line
    tags: Mapped[list[str]] = jsonb_list()
    # What a model understood from the file (photo description, voice transcript, PDF text).
    description: Mapped[str] = mapped_column(Text, default="")
    understanding: Mapped[UnderstandingStatus] = enum_col(
        UnderstandingStatus, default=UnderstandingStatus.SKIPPED)
    understanding_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Generated media: the generation that made it.
    generation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# --- agent work ------------------------------------------------------------------------------


class ReplyRun(Base):
    """One persona turn: what she knew (`context`), what she decided (`plan`), what the
    guard said."""

    __tablename__ = "reply_run"
    __table_args__ = (Index("ix_reply_run_chat", "chat_id", "id"),)

    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    status: Mapped[RunStatus] = enum_col(RunStatus, default=RunStatus.RUNNING)
    trigger_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Snapshot of the rendered context (facts, memory, timeline window) sent to the model.
    context: Mapped[dict[str, Any]] = jsonb_dict()
    plan: Mapped[dict[str, Any]] = jsonb_dict()
    guard_verdicts: Mapped[list[dict[str, Any]]] = jsonb_list()
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)


class Generation(Base):
    """A photo / video / voice the persona asked a model to make."""

    __tablename__ = "generation"

    persona_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"),
                                            index=True)
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("chat.id", ondelete="SET NULL"),
                                                nullable=True)
    reply_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("reply_run.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[GenerationKind] = enum_col(GenerationKind)
    prompt: Mapped[str] = mapped_column(Text)
    # Exact words she says on camera (video note, video with speech); empty = no speech.
    speech: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    caption: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = jsonb_list()
    ref_media_ids: Mapped[list[int]] = jsonb_list()
    # The scene it was made in: {day, event_id, place, outfit, ambience} (sim/domain/scene.py);
    # later photos of the same event use its photo as a continuity reference.
    scene: Mapped[dict[str, Any]] = jsonb_dict()
    status: Mapped[GenerationStatus] = enum_col(GenerationStatus, default=GenerationStatus.QUEUED)
    model: Mapped[str] = mapped_column(String(160), default="")
    result_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL"), nullable=True)
    broker_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)


class Call(Base):
    """A voice call with the persona (Telegram private call + Broker live)."""

    __tablename__ = "call"

    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"), index=True)
    direction: Mapped[CallDirection] = enum_col(CallDirection)
    status: Mapped[CallStatus] = enum_col(CallStatus, default=CallStatus.RINGING)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTimeUTC, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    # [{"who": "client"|"persona", "text": str, "t": seconds_from_start}]
    transcript: Mapped[list[dict[str, Any]]] = jsonb_list()
    model: Mapped[str] = mapped_column(String(160), default="")
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChatEvent(Base):
    """The chat's timeline: the dashboard journal AND the only input of the context builder.

    Append-only. `in_context` = the agent sees it (system steps such as a guard block are shown
    to the operator but may be hidden from the agent). Detailed rows live in their own tables and
    are referenced by id; `data` holds the small facts the renderer needs."""

    __tablename__ = "chat_event"
    __table_args__ = (Index("ix_chat_event_chat", "chat_id", "id"),)

    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    kind: Mapped[EventKind] = enum_col(EventKind)
    in_context: Mapped[bool] = mapped_column(Boolean, default=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("message.id", ondelete="SET NULL"), nullable=True)
    media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL"), nullable=True)
    call_id: Mapped[int | None] = mapped_column(
        ForeignKey("call.id", ondelete="SET NULL"), nullable=True)
    generation_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation.id", ondelete="SET NULL"), nullable=True)
    reply_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("reply_run.id", ondelete="SET NULL"), nullable=True)
    # client | persona | operator:<id> | system
    actor: Mapped[str] = mapped_column(String(64), default="system")
    data: Mapped[dict[str, Any]] = jsonb_dict()


class LlmCall(Base):
    """Every Broker call: what went in, what came back, cost. Shown under each persona turn."""

    __tablename__ = "llm_call"
    __table_args__ = (Index("ix_llm_call_run", "reply_run_id"),)

    reply_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("reply_run.id", ondelete="SET NULL"), nullable=True)
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("chat.id", ondelete="SET NULL"),
                                                nullable=True, index=True)
    purpose: Mapped[LlmPurpose] = enum_col(LlmPurpose)
    model: Mapped[str] = mapped_column(String(160), default="")
    request: Mapped[dict[str, Any]] = jsonb_dict()
    response: Mapped[dict[str, Any]] = jsonb_dict()
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
