"""Channel port — one interface over Telegram / WhatsApp / Instagram.

Swapping a transport (e.g. Evolution for the WhatsApp Cloud API) is a new adapter that
implements this Protocol; services never change."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sim.domain.enums import ChannelKind, SessionStatus


@dataclass(frozen=True)
class InboundMessage:
    external_thread_id: str              # chat id / remote jid / IG thread id
    external_user_id: str                # the client's stable id in this channel
    text: str
    occurred_at: datetime                # naive UTC
    external_id: str | None = None       # channel message id (dedup key)
    user_name: str | None = None         # display name
    user_handle: str | None = None       # @username
    avatar_url: str | None = None
    phone: str | None = None             # WhatsApp: the address IS the phone
    media_kind: str | None = None        # image | video | audio | voice
    media_url: str | None = None
    is_edit: bool = False                # the sender edited an earlier message (not a new turn)
    from_me: bool = False                # sent from the account itself (phone app) — not a client turn


@dataclass(frozen=True)
class SendResult:
    ok: bool
    external_message_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ConnectResult:
    status: SessionStatus
    qr_data_uri: str | None = None
    need: str | None = None              # "2fa" | "challenge"
    message: str | None = None
    account_label: str | None = None     # @handle / phone / bot username
    session: dict[str, object] = field(default_factory=dict)  # to persist (encrypted)


class ChannelPort(Protocol):
    kind: ChannelKind

    async def send_text(self, external_thread_id: str, text: str) -> SendResult: ...

    async def session_status(self) -> SessionStatus: ...


class PollingChannelPort(ChannelPort, Protocol):
    """Channels without push delivery (Instagram private API)."""

    async def fetch_inbound(self) -> list[InboundMessage]: ...
