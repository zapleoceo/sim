"""Request bodies (pydantic) and response serialisers (plain dicts, shapes from docs/api.md)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sim.adapters.db.models import (
    AdminUser,
    Client,
    ContentPost,
    Dialog,
    FunnelEvent,
    Message,
    PartnerEvent,
    Persona,
    Strategy,
)
from sim.adapters.db.models import Channel as ChannelRow
from sim.domain.enums import ChannelKind, ClientStatus, ContentKind, Stage
from sim.services.channels import ConnectState


def dt(value: datetime | None) -> str | None:
    """Naive UTC -> ISO-8601 with a Z suffix."""
    return value.isoformat() + "Z" if value else None


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---- inputs -------------------------------------------------------------------------------
class LoginIn(Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PersonaIn(Body):
    name: str = Field(min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=0, le=120)
    gender: Literal["female", "male", "other"] = "other"
    avatar_url: str = Field(default="", max_length=500)
    tagline: str = Field(default="", max_length=300)
    character: str = ""
    backstory: str = ""
    communication_style: str = ""
    language: str = Field(default="ru", max_length=64)
    model: str = Field(default="", max_length=120)
    voice_id: str = Field(default="", max_length=120)
    strategy_id: int | None = None
    is_active: bool = True
    disclosure_text: str = ""


class PreviewHistoryItem(Body):
    role: Literal["user", "assistant"]
    text: str


class PreviewIn(Body):
    message: str = Field(min_length=1, max_length=4000)
    history: list[PreviewHistoryItem] = Field(default_factory=list, max_length=60)


class StrategyIn(Body):
    name: str = Field(min_length=1, max_length=120)
    goal: str = ""
    offer: str = ""
    partner_name: str = Field(default="", max_length=120)
    registration_url: str = Field(default="", max_length=500)
    stage_playbook: dict[str, str] = Field(default_factory=dict)
    rules: list[str] = Field(default_factory=list)
    followup_hours: list[float] = Field(default_factory=list)
    reply_delay_s: list[float] = Field(default_factory=lambda: [20.0, 90.0])
    notes: str = ""

    @field_validator("reply_delay_s")
    @classmethod
    def two_ordered(cls, v: list[float]) -> list[float]:
        if len(v) != 2 or v[0] < 0 or v[0] > v[1]:
            raise ValueError("reply_delay_s must be [min, max] with 0 <= min <= max")
        return v

    @field_validator("followup_hours")
    @classmethod
    def ascending(cls, v: list[float]) -> list[float]:
        if any(h <= 0 for h in v) or v != sorted(v):
            raise ValueError("followup_hours must be positive and ascending")
        return v

    @field_validator("stage_playbook")
    @classmethod
    def known_stages(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - {s.value for s in Stage}
        if unknown:
            raise ValueError(f"unknown stages: {sorted(unknown)}")
        return v


class ChannelIn(Body):
    kind: ChannelKind
    persona_id: int
    name: str = Field(default="", max_length=120)


class VerifyIn(Body):
    code: str = Field(min_length=1, max_length=64)


class OperatorMessageIn(Body):
    text: str = Field(min_length=1, max_length=4000)


class AiToggleIn(Body):
    paused: bool


class ClientPatchIn(Body):
    notes: str | None = None
    next_step: str | None = None
    next_step_at: datetime | None = None
    display_name: str | None = Field(default=None, max_length=200)


class StageChangeIn(Body):
    stage: Stage | None = None
    status: ClientStatus | None = None
    reason: str = Field(default="", max_length=1000)


class ContentIn(Body):
    persona_id: int
    channel_id: int | None = None
    kind: ContentKind = ContentKind.PHOTO
    caption: str = ""
    media_urls: list[str] = Field(default_factory=list)
    scheduled_at: datetime | None = None


class PartnerHookIn(Body):
    client_ref: str = Field(min_length=1, max_length=64)
    event: Literal["registration", "payment"]
    external_id: str = Field(min_length=1, max_length=200)
    amount: float | None = None
    currency: str = Field(default="", max_length=8)
    payout: float | None = None
    occurred_at: datetime


# ---- outputs ------------------------------------------------------------------------------
def user_out(u: AdminUser) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "role": u.role.value}


def persona_out(p: Persona) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "age": p.age, "gender": p.gender, "avatar_url": p.avatar_url,
        "tagline": p.tagline, "character": p.character, "backstory": p.backstory,
        "communication_style": p.communication_style, "language": p.language, "model": p.model,
        "voice_id": p.voice_id, "strategy_id": p.strategy_id, "is_active": p.is_active,
        "disclosure_text": p.disclosure_text, "created_at": dt(p.created_at),
    }


def strategy_out(s: Strategy) -> dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "goal": s.goal, "offer": s.offer,
        "partner_name": s.partner_name, "registration_url": s.registration_url,
        "stage_playbook": s.stage_playbook, "rules": s.rules, "followup_hours": s.followup_hours,
        "reply_delay_s": s.reply_delay_s, "notes": s.notes, "created_at": dt(s.created_at),
    }


def channel_out(c: ChannelRow) -> dict[str, Any]:
    return {
        "id": c.id, "kind": c.kind.value, "name": c.name, "persona_id": c.persona_id,
        "status": c.status.value, "account_label": c.account_label, "last_error": c.last_error,
        "last_activity_at": dt(c.last_activity_at),
    }


def connect_state_out(s: ConnectState) -> dict[str, Any]:
    out: dict[str, Any] = {"status": s.status.value}
    for key, value in (("qr_data_uri", s.qr_data_uri), ("need", s.need), ("message", s.message)):
        if value is not None:
            out[key] = value
    return out


def message_out(m: Message) -> dict[str, Any]:
    return {
        "id": m.id, "direction": m.direction.value, "author": m.author.value, "text": m.text,
        "media_kind": m.media_kind, "media_url": m.media_url, "created_at": dt(m.created_at),
        "delivery": m.delivery.value, "model": m.model,
    }


def dialog_out(d: Dialog) -> dict[str, Any]:
    return {
        "id": d.id, "channel_id": d.channel_id, "channel_kind": d.channel.kind.value,
        "persona_id": d.client.persona_id, "client_id": d.client_id,
        "external_thread_id": d.external_thread_id, "ai_paused": d.ai_paused,
        "needs_operator": d.needs_operator, "operator_id": d.operator_id, "unread": d.unread,
        "created_at": dt(d.created_at),
    }


def client_mini(c: Client) -> dict[str, Any]:
    return {
        "id": c.id, "display_name": c.display_name, "avatar_url": c.avatar_url,
        "stage": c.stage.value, "status": c.status.value, "last_contact_at": dt(c.last_contact_at),
    }


def client_out(c: Client, channels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": c.id, "display_name": c.display_name, "avatar_url": c.avatar_url,
        "persona_id": c.persona_id, "stage": c.stage.value, "status": c.status.value,
        "next_step": c.next_step, "next_step_at": dt(c.next_step_at), "notes": c.notes,
        "needs": c.needs or [], "channels": channels, "ref_code": c.ref_code,
        "last_contact_at": dt(c.last_contact_at), "created_at": dt(c.created_at),
    }


def funnel_event_out(e: FunnelEvent) -> dict[str, Any]:
    is_stage = e.kind == "stage"
    return {
        "id": e.id, "created_at": dt(e.created_at), "type": e.kind,
        "from_stage": e.from_value if is_stage else None,
        "to_stage": e.to_value if is_stage else None,
        "from_status": None if is_stage else e.from_value,
        "to_status": None if is_stage else e.to_value,
        "actor": e.actor, "reason": e.reason,
    }


def partner_event_out(e: PartnerEvent) -> dict[str, Any]:
    return {
        "id": e.id, "event": e.kind.value, "occurred_at": dt(e.occurred_at),
        "amount": e.amount, "currency": e.currency, "payout": e.payout,
        "external_id": e.external_id,
    }


def content_out(p: ContentPost) -> dict[str, Any]:
    return {
        "id": p.id, "persona_id": p.persona_id, "channel_id": p.channel_id, "kind": p.kind.value,
        "caption": p.caption, "media_urls": p.media_urls or [], "status": p.status.value,
        "scheduled_at": dt(p.scheduled_at), "published_at": dt(p.published_at), "error": p.error,
    }


def dialog_row(d: Dialog, persona: Persona | None) -> dict[str, Any]:
    c = d.client
    return {
        "id": d.id,
        "channel_kind": d.channel.kind.value,
        "persona": {
            "id": c.persona_id,
            "name": persona.name if persona else "",
            "avatar_url": persona.avatar_url if persona else "",
        },
        "client": {
            "id": c.id, "display_name": c.display_name, "avatar_url": c.avatar_url,
            "stage": c.stage.value, "status": c.status.value,
        },
        "last_message": {
            "text": d.last_message_text,
            "direction": d.last_message_direction.value if d.last_message_direction else None,
            "at": dt(d.last_message_at),
        },
        "unread": d.unread,
        "ai_paused": d.ai_paused,
        "needs_operator": d.needs_operator,
    }
