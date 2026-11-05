"""kind -> Connector. The one place that knows which channels exist.

Insertion order is display order. Adding a channel = a module + one line here."""
from __future__ import annotations

from sim.connectors.instagram import InstagramConnector
from sim.connectors.spec import Connector
from sim.connectors.telegram import TelegramConnector
from sim.connectors.telegram_user import TelegramUserConnector
from sim.connectors.whatsapp import WhatsAppConnector
from sim.domain.enums import ChannelKind

REGISTRY: dict[ChannelKind, Connector] = {
    ChannelKind.TELEGRAM: TelegramConnector(),
    ChannelKind.TELEGRAM_USER: TelegramUserConnector(),
    ChannelKind.WHATSAPP: WhatsAppConnector(),
    ChannelKind.INSTAGRAM: InstagramConnector(),
}


def connector_for(kind: ChannelKind | str) -> Connector:
    try:
        return REGISTRY[ChannelKind(kind)]
    except (KeyError, ValueError):
        raise ValueError(f"No connector registered for channel kind {kind!r}") from None
