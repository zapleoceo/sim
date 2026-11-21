"""Every closed set of values in SIM. Stored in the database as plain strings (non-native enums)."""
from __future__ import annotations

from enum import StrEnum


class ChannelKind(StrEnum):
    TELEGRAM_USER = "telegram_user"     # a personal Telegram account over MTProto (Telethon)


class ChannelStatus(StrEnum):
    NEW = "new"
    CONNECTED = "connected"
    ERROR = "error"
    DISABLED = "disabled"


class Direction(StrEnum):
    IN = "in"       # from the client
    OUT = "out"     # to the client


class Author(StrEnum):
    CLIENT = "client"
    PERSONA = "persona"     # written by the model
    OPERATOR = "operator"   # written by a human in the dashboard, sent as the persona


class MessageKind(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    FILE = "file"
    STICKER = "sticker"


class MessageStatus(StrEnum):
    PENDING = "pending"     # stored, not yet handed to Telegram
    SENT = "sent"
    FAILED = "failed"
    RECEIVED = "received"   # incoming messages


class MediaSource(StrEnum):
    LIBRARY = "library"         # uploaded by the owner in the dashboard
    GENERATED = "generated"     # made by a model for the persona
    INCOMING = "incoming"       # sent by a client


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"


class UnderstandingStatus(StrEnum):
    """Whether an incoming attachment has been turned into text the agent can read."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"     # nothing to understand (e.g. sticker without a model)


class RunStatus(StrEnum):
    """One persona turn (`reply_run`)."""

    RUNNING = "running"
    SENT = "sent"
    BLOCKED = "blocked"     # the guard refused twice: an operator is needed
    SKIPPED = "skipped"     # nothing to answer (AI off, superseded by a newer run)
    FAILED = "failed"


class GenerationKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class CallDirection(StrEnum):
    IN = "in"
    OUT = "out"


class CallStatus(StrEnum):
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    MISSED = "missed"
    DECLINED = "declined"
    FAILED = "failed"


class LlmPurpose(StrEnum):
    REPLY = "reply"
    GUARD = "guard"
    VISION = "vision"
    TRANSCRIBE = "transcribe"
    DOCUMENT = "document"
    TTS = "tts"
    IMAGE = "image"
    VIDEO = "video"
    LIVE = "live"
    MEMORY = "memory"


class EventKind(StrEnum):
    """Everything that happens in a chat. One table (`chat_event`) is both the dashboard journal
    and the only source the context builder reads (docs/context.md)."""

    MESSAGE_IN = "message_in"               # client message (any kind)
    MESSAGE_OUT = "message_out"             # persona or operator message handed to Telegram
    MESSAGE_EDITED = "message_edited"       # client edited a message
    MESSAGE_DELETED = "message_deleted"     # client deleted a message
    MEDIA_UNDERSTOOD = "media_understood"   # an attachment got its description / transcript
    SEND_FAILED = "send_failed"
    CALL = "call"                           # a call finished (any outcome), with transcript
    GENERATION_STARTED = "generation_started"
    GENERATION_FAILED = "generation_failed"
    GUARD_BLOCKED = "guard_blocked"
    OPERATOR_NEEDED = "operator_needed"
    AI_TOGGLED = "ai_toggled"
    OPERATOR_NOTE = "operator_note"         # a note for the agent typed by the operator
    CLIENT_UPDATED = "client_updated"       # profile fields changed (name, phone, notes)
    MEMORY_UPDATED = "memory_updated"       # client memory / chat summary refreshed
