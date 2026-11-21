"""Route handlers of the dashboard API, one module per section of docs/api.md."""
from __future__ import annotations

from typing import Any

from sim.api.routes import agents, auth, channels, chats, clients, events, life, media, persona

API_ROUTES: list[Any] = [
    *auth.ROUTES, *chats.ROUTES, *persona.ROUTES, *life.ROUTES, *channels.ROUTES,
    *media.ROUTES, *clients.ROUTES, *events.ROUTES, *agents.ROUTES,
]
