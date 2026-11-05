"""Channel lifecycle: create / connect / verify / state / disconnect, delegated to the connector
registry. Whatever session dict a connector returns is stored encrypted."""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import Channel
from sim.adapters.db.repositories.catalog import ChannelRepo, PersonaRepo
from sim.clock import Clock, utcnow
from sim.connectors.spec import Connector
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import ConnectResult

log = logging.getLogger(__name__)


class ChannelError(Exception):
    """Invalid channel operation (unknown persona, no connector, ...)."""


@dataclass(frozen=True)
class ConnectState:
    status: SessionStatus
    qr_data_uri: str | None = None
    need: str | None = None
    message: str | None = None


class ChannelService:
    def __init__(
        self,
        session: AsyncSession,
        registry: Mapping[ChannelKind, Connector],
        box: SecretBox,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self.registry = registry
        self.box = box
        self.clock = clock
        self.channels = ChannelRepo(session)
        self.personas = PersonaRepo(session)

    def connector(self, channel: Channel) -> Connector:
        connector = self.registry.get(channel.kind)
        if connector is None:
            raise ChannelError(f"no connector for {channel.kind.value}")
        return connector

    async def list(self) -> Sequence[Channel]:
        return await self.channels.visible()

    async def create(self, kind: ChannelKind, persona_id: int, name: str) -> Channel:
        if await self.personas.get(persona_id) is None:
            raise ChannelError("persona not found")
        if kind not in self.registry:
            raise ChannelError(f"channel kind {kind.value} is not available")
        return await self.channels.add(Channel(kind=kind, persona_id=persona_id, name=name))

    async def connect(self, channel: Channel, credentials: Mapping[str, str]) -> ConnectState:
        channel.status = SessionStatus.PENDING
        return await self.run(
            channel,
            lambda c, s: self.connector(channel).connect(channel.id, credentials, s),
        )

    async def verify(self, channel: Channel, code: str) -> ConnectState:
        return await self.run(channel, lambda c, s: self.connector(channel).verify(channel.id, code, s))

    async def state(self, channel: Channel) -> ConnectState:
        return await self.run(channel, lambda c, s: self.connector(channel).state(channel.id, s))

    async def remove(self, channel: Channel) -> None:
        """Unlink the account. The row is deleted unless dialogs reference it (then archived)."""
        session = self.box.decrypt_json(channel.session_enc)
        connector = self.registry.get(channel.kind)
        if connector is not None and session:
            try:
                await connector.disconnect(channel.id, session)
            except Exception:  # unlinking must not be blocked by a dead remote account
                log.warning("disconnect failed for channel %s", channel.id, exc_info=True)
        channel.session_enc = None
        channel.status = SessionStatus.NEW
        if await self.channels.has_dialogs(channel.id):
            channel.is_archived = True
        else:
            await self.channels.delete(channel)

    async def run(self, channel: Channel, call) -> ConnectState:  # noqa: ANN001
        session = self.box.decrypt_json(channel.session_enc)
        try:
            result: ConnectResult = await call(channel, session)
        except Exception as exc:  # connector/network errors become a visible channel error
            message = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("channel %s connector error: %s", channel.id, message)
            channel.status = SessionStatus.ERROR
            channel.last_error = message
            return ConnectState(SessionStatus.ERROR, message=message)
        channel.status = result.status
        if result.account_label:
            channel.account_label = result.account_label
        channel.last_error = result.message if result.status is SessionStatus.ERROR else None
        if result.session:
            channel.session_enc = self.box.encrypt_json(result.session)
        channel.last_activity_at = self.clock()
        return ConnectState(result.status, result.qr_data_uri, result.need, result.message)
