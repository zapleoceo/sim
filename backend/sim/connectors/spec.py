"""What one connector IS. Services ask the registry; they never branch on `kind`.

Adding a channel = a new module that implements Connector and one line in registry.REGISTRY.
`session` is the decrypted per-channel state the connector asked to persist (tokens, cookies,
instagrapi settings, webhook secret). The service layer encrypts/decrypts it; connectors only
read and return it."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from sim.domain.enums import ChannelKind
from sim.ports.channel import ChannelPort, ConnectResult, InboundMessage

Session = dict[str, Any]


class Connector(Protocol):
    kind: ChannelKind
    label: str
    # True: the worker calls build_port(...).fetch_inbound() on a timer (Instagram).
    # False: messages arrive through parse_webhook (Telegram, WhatsApp).
    polling: bool

    async def connect(
        self, channel_id: int, credentials: Mapping[str, str], session: Session
    ) -> ConnectResult:
        """Start or finish linking the account. ConnectResult.session is persisted."""
        ...

    async def verify(self, channel_id: int, code: str, session: Session) -> ConnectResult:
        """Second step: 2FA / checkpoint code (Instagram). Others answer status unchanged."""
        ...

    async def state(self, channel_id: int, session: Session) -> ConnectResult:
        """Current link state (and a fresh QR for WhatsApp while pending)."""
        ...

    async def disconnect(self, channel_id: int, session: Session) -> None: ...

    def build_port(self, channel_id: int, session: Session) -> ChannelPort: ...

    def verify_webhook(self, headers: Mapping[str, str], session: Session) -> bool:
        """Authenticity of an incoming webhook (secret token / apikey). Polling: False."""
        ...

    def parse_webhook(self, payload: Mapping[str, Any]) -> list[InboundMessage]: ...
