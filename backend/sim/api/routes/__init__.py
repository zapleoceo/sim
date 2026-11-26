"""Route handlers of the dashboard API, one module per section of docs/api.md."""
from __future__ import annotations

from typing import Any

from sim.api.routes import (
    agents,
    auth,
    calls,
    channels,
    chats,
    clients,
    contacts,
    diary,
    events,
    hooks,
    life,
    media,
    persona,
    stickers,
)

API_ROUTES: list[Any] = [
    *auth.ROUTES, *chats.ROUTES, *persona.ROUTES, *life.ROUTES, *channels.ROUTES,
    *media.ROUTES, *clients.ROUTES, *events.ROUTES, *agents.ROUTES, *diary.ROUTES,
    *hooks.ROUTES, *contacts.ROUTES, *calls.ROUTES, *stickers.ROUTES,
]
