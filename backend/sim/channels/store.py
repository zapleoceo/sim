"""A channel's stored account, shared by the Instagram and WhatsApp runtimes: credentials are
Fernet-encrypted JSON in `channel.credentials_enc` (sim/crypto.py), exactly like Telegram's, and
are never logged or returned by the API."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.crypto import CryptoError, decrypt_json, encrypt_json
from sim.db.models import Channel, Persona
from sim.db.session import transaction
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import PortError


@dataclass(frozen=True)
class StoredAccount:
    channel_id: int
    persona_id: int
    kind: ChannelKind
    status: ChannelStatus
    token: str = field(repr=False)              # the encrypted blob: changes on a new login
    credentials: dict[str, Any] = field(repr=False)


class ChannelStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], secret_key: str) -> None:
        self.sessions = sessions
        self.secret_key = secret_key

    async def account(self, channel_id: int, kind: ChannelKind) -> StoredAccount:
        """The usable account of a channel; PortError (permanent) when the channel is gone,
        of another kind, switched off or has no readable credentials."""
        async with transaction(self.sessions) as session:
            channel = await session.get(Channel, channel_id)
            if channel is None or channel.kind != kind:
                raise PortError(f"{kind} channel {channel_id} not found")
            if channel.status == ChannelStatus.DISABLED:
                raise PortError(f"channel {channel_id} is switched off")
            token = channel.credentials_enc or ""
            snapshot = (channel.persona_id, ChannelStatus(channel.status))
        if not token:
            raise PortError(f"channel {channel_id} has no stored account")
        try:
            credentials = decrypt_json(token, self.secret_key)
        except CryptoError as exc:
            raise PortError(f"channel {channel_id}: {exc}") from None
        return StoredAccount(channel_id, snapshot[0], kind, snapshot[1], token, credentials)

    async def save(self, channel_id: int, credentials: dict[str, Any]) -> None:
        """Store refreshed credentials (Instagram's cookies change as it is used)."""
        token = encrypt_json(credentials, self.secret_key)
        async with transaction(self.sessions) as session:
            channel = await session.get(Channel, channel_id)
            if channel is not None and channel.status != ChannelStatus.DISABLED:
                channel.credentials_enc = token

    async def set_status(self, channel_id: int, status: ChannelStatus, error: str | None, *,
                         at: datetime | None = None) -> None:
        """A switched-off channel keeps its status (the owner's choice wins)."""
        async with transaction(self.sessions) as session:
            channel = await session.get(Channel, channel_id)
            if channel is None or channel.status == ChannelStatus.DISABLED:
                return
            channel.status = status
            channel.last_error = error[:500] if error else None
            if at is not None:
                channel.last_activity_at = at

    async def connected(self, kind: ChannelKind) -> list[int]:
        """Connected channels of this kind whose persona is active."""
        async with transaction(self.sessions) as session:
            rows = await session.scalars(
                select(Channel.id).join(Persona, Persona.id == Channel.persona_id)
                .where(Channel.kind == kind, Channel.status == ChannelStatus.CONNECTED,
                       Channel.credentials_enc.is_not(None), Persona.is_active.is_(True))
                .order_by(Channel.id))
            return list(rows)
