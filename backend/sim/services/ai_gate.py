"""The AI gate (`sim.domain.ai_gate.ai_block`) as a query: the chats the AI may answer in, without
loading them one by one. Same conditions, same meaning — a test keeps the two in step."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Select, and_
from sqlalchemy.sql.elements import ColumnElement

from sim.db.models import Chat, Client, Persona


def ai_open() -> ColumnElement[bool]:
    """`ai_block(persona, client, chat) is None` for a query that joins `Chat` with its
    `Client` and its own `Persona` (see `only_ai_open`)."""
    return and_(Persona.is_active.is_(True), Persona.ai_enabled.is_(True),
                Client.ai_enabled.is_(True), Chat.ai_enabled.is_(True),
                Chat.needs_operator.is_(False))


def only_ai_open(stmt: Select[Any]) -> Select[Any]:
    """Keep the chats of a `Chat` query the AI may answer in."""
    return (stmt.join(Client, Client.id == Chat.client_id)
            .join(Persona, Persona.id == Chat.persona_id)
            .where(ai_open()))
