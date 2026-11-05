"""Domain vocabulary. Values are stored in the DB and returned by the API — never rename one."""
from __future__ import annotations

from enum import StrEnum


class ChannelKind(StrEnum):
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


class SessionStatus(StrEnum):
    NEW = "new"
    PENDING = "pending"        # waiting for QR scan / login in progress
    ACTIVE = "active"
    CHALLENGE = "challenge"    # 2FA or checkpoint code required
    EXPIRED = "expired"
    ERROR = "error"


class Stage(StrEnum):
    """Funnel stages in order. The order of declaration IS the funnel order."""

    FIRST_CONTACT = "first_contact"
    ENGAGED = "engaged"
    NEEDS_IDENTIFIED = "needs_identified"
    INTERESTED = "interested"
    HANDED_TO_PARTNER = "handed_to_partner"
    REGISTERED = "registered"
    PAID = "paid"


class ClientStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    NO_RESPONSE = "no_response"
    REFUSED = "refused"


class Author(StrEnum):
    CLIENT = "client"
    AI = "ai"
    OPERATOR = "operator"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class Delivery(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"   # superseded (operator took over / AI paused) before it was sent


class Role(StrEnum):
    SUPERADMIN = "superadmin"   # owner rights + manages the admins
    OWNER = "owner"
    OPERATOR = "operator"

    @property
    def has_owner_rights(self) -> bool:
        """The single place that says who may do owner-only things (settings, approvals)."""
        return self in (Role.SUPERADMIN, Role.OWNER)


class PartnerEventKind(StrEnum):
    REGISTRATION = "registration"
    PAYMENT = "payment"


class ContentKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    REEL = "reel"
    STORY = "story"


class ContentStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
