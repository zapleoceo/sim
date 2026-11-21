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
    VIDEO_NOTE = "video_note"       # a round video message («кружок»), made like a video


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
    LIFE = "life"           # the daily planner of the persona's life (docs/persona-life.md)
    PASSPORT = "passport"   # the client passport writer, «паспортистка» (docs/leads.md)
    ATTENTION = "attention"  # «Внимание»: when she reacts to notifications (docs/attention.md)


class ArcStatus(StrEnum):
    """A story arc of the persona's life (`persona_arc`)."""

    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"


class DaySource(StrEnum):
    """Who wrote a planned day (`persona_day`)."""

    MODEL = "model"         # the daily planner
    EDITED = "edited"       # changed by the owner in the dashboard; never re-planned by itself


class CanonStatus(StrEnum):
    """A fact the persona invented about herself (`persona_canon`)."""

    ACTIVE = "active"
    REJECTED = "rejected"   # rejected by the owner: hidden from the agent, never added again


class LeadStage(StrEnum):
    """Where a client is on the way to the offer (`client.passport.stage`, docs/leads.md).
    Kept inside JSONB, so a new value needs no migration."""

    NEW = "new"                     # just started talking
    ACQUAINTED = "acquainted"       # got to know each other
    INTERESTED = "interested"       # showed interest in the offer
    WARM = "warm"                   # trusts her, talks willingly
    HOT = "hot"                     # ready for the next step
    CLIENT = "client"               # became a client
    LOST = "lost"                   # went silent or refused


class ClonePart(StrEnum):
    """What «Клонировать» copies from one persona to a new one (docs/personas.md). One copier
    per part in sim/services/persona_clone.py."""

    BASICS = "basics"               # gender, language, time zone, birth date
    PROMPT = "prompt"
    AGENTS = "agents"               # every agent: model, key tier, prompt, max_tokens; «Внимание»
    VOICE = "voice"                 # voice, manner of speech, background in voice notes
    GUARD = "guard"                 # the guard's rules
    APPEARANCE = "appearance"
    WORLD = "world"                 # routine, people, hobbies, places (`persona.life`)
    ARCS = "arcs"                   # story arcs
    MEDIA = "media"                 # library photos and videos, files copied
    SWITCHES = "switches"           # AI / voice / photo / video / calls, reply delay
    CANON = "canon"                 # facts she invented about herself


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
    MEMORY_UPDATED = "memory_updated"       # the chat summary was refreshed
    ATTENTION = "attention"                 # she looked at the chat: read, later, now…
