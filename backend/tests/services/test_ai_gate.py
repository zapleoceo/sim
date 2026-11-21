"""The AI gate (sim/domain/ai_gate.py, docs/reply.md «Может ли ИИ отвечать в чате»): one rule
for incoming messages, phone checks, reply turns, calls, the team's status and waking chats.
Every reason in its order, and the query form (sim/services/ai_gate.py) agrees with it."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Client, Persona
from sim.domain.ai_gate import AiBlock, ai_block, persona_block
from sim.services.ai_gate import only_ai_open
from tests.services.factories import make_chat, make_persona


def persona(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"id": 1, "is_active": True, "ai_enabled": True, **fields})


def client(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"ai_enabled": True, **fields})


def chat(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"persona_id": 1, "ai_enabled": True, "needs_operator": False,
                              **fields})


# (persona, client, chat) -> the first reason, in the gate's order.
CASES: list[tuple[str, Any, Any, Any, AiBlock | None]] = [
    ("all on", persona(), client(), chat(), None),
    ("no persona", None, client(), chat(), AiBlock.NO_PERSONA),
    ("another persona's chat", persona(id=2), client(), chat(), AiBlock.NO_PERSONA),
    ("archived", persona(is_active=False), client(), chat(), AiBlock.ARCHIVED),
    ("archived wins over AI off", persona(is_active=False, ai_enabled=False), client(), chat(),
     AiBlock.ARCHIVED),
    ("persona AI off", persona(ai_enabled=False), client(), chat(), AiBlock.PERSONA_OFF),
    ("persona before client", persona(ai_enabled=False), client(ai_enabled=False), chat(),
     AiBlock.PERSONA_OFF),
    ("no client", persona(), None, chat(), AiBlock.CLIENT_OFF),
    ("client AI off", persona(), client(ai_enabled=False), chat(), AiBlock.CLIENT_OFF),
    ("client before chat", persona(), client(ai_enabled=False), chat(ai_enabled=False),
     AiBlock.CLIENT_OFF),
    ("chat AI off", persona(), client(), chat(ai_enabled=False), AiBlock.CHAT_OFF),
    ("AI off wins over the operator", persona(), client(),
     chat(ai_enabled=False, needs_operator=True), AiBlock.CHAT_OFF),
    ("waiting for an operator", persona(), client(), chat(needs_operator=True),
     AiBlock.NEEDS_OPERATOR),
]


@pytest.mark.parametrize(("name", "who", "lead", "where", "expected"), CASES,
                         ids=[case[0] for case in CASES])
def test_the_first_reason_wins(name: str, who: Any, lead: Any, where: Any,
                               expected: AiBlock | None) -> None:
    assert ai_block(who, lead, where) == expected


def test_the_persona_part_alone() -> None:
    """A phone check covers all her chats: only her own part of the rule."""
    assert persona_block(None) == AiBlock.NO_PERSONA
    assert persona_block(persona(is_active=False)) == AiBlock.ARCHIVED
    assert persona_block(persona(ai_enabled=False)) == AiBlock.PERSONA_OFF
    assert persona_block(persona(id=99)) is None        # no chat: whose chat it is is not asked


async def test_the_query_agrees_with_the_rule(db: AsyncSession) -> None:
    """`only_ai_open` keeps exactly the chats `ai_block` lets the AI answer in."""
    marina = await make_persona(db)
    archived = await make_persona(db, name="Архив", is_active=False)
    off = await make_persona(db, name="Выкл", ai_enabled=False)
    chats = [
        await make_chat(db, marina),
        await make_chat(db, marina, client_ai=False),
        await make_chat(db, marina, chat_ai=False),
        await make_chat(db, marina),
        await make_chat(db, archived),
        await make_chat(db, off),
    ]
    chats[3].needs_operator = True
    await db.flush()
    ids = [c.id for c in chats]

    found = set(await db.scalars(only_ai_open(select(Chat.id).where(Chat.id.in_(ids)))))

    expected = set()
    for item in chats:
        owner = await db.get(Persona, item.persona_id)
        lead = await db.get(Client, item.client_id)
        if ai_block(owner, lead, item) is None:
            expected.add(item.id)
    assert found == expected == {chats[0].id}
