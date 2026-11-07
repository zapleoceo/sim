"""Authenticated message history and owner-only LLM attempt audit."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sim.adapters.db.models import Client
from sim.adapters.db.repositories.activity import ActivityFilter, ActivityRepo
from sim.api.activity_schemas import llm_activity_out, message_activity_out
from sim.api.date_bounds import optional_bounds
from sim.api.deps import Db, authed, owner_only
from sim.domain.enums import Author, ChannelKind, Delivery, Direction

router = APIRouter(tags=["activity"], dependencies=[Depends(authed)])


class CommonQuery(BaseModel):
    persona_id: int | None = Field(default=None, gt=0)
    channel_id: int | None = Field(default=None, gt=0)
    channel_kind: ChannelKind | None = None
    q: str | None = Field(default=None, max_length=200)
    from_: str | None = Field(default=None, alias="from", max_length=40)
    to: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    def filters(self, **extra) -> ActivityFilter:
        start, end = optional_bounds(self.from_, self.to)
        return ActivityFilter(
            persona_id=self.persona_id, channel_id=self.channel_id, channel_kind=self.channel_kind,
            q=self.q, start=start, end=end, **extra,
        )


class ClientMessageQuery(CommonQuery):
    direction: Direction | None = None
    author: Author | None = None
    delivery: Delivery | None = None

    def message_filters(self, client_id: int | None) -> ActivityFilter:
        return self.filters(client_id=client_id, direction=self.direction, author=self.author, delivery=self.delivery)


class MessageQuery(ClientMessageQuery):
    client_id: int | None = Field(default=None, gt=0)


class LlmQuery(CommonQuery):
    client_id: int | None = Field(default=None, gt=0)
    workflow: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, max_length=64)
    has_error: bool | None = None


async def message_page(s: Db, query: ClientMessageQuery, client_id: int | None):
    rows, total = await ActivityRepo(s).messages(query.message_filters(client_id), query.limit, query.offset)
    return {"items": [message_activity_out(*row) for row in rows], "total": total,
            "limit": query.limit, "offset": query.offset}


@router.get("/api/activity/messages")
async def messages(s: Db, query: Annotated[MessageQuery, Query()]):
    return await message_page(s, query, query.client_id)


@router.get("/api/clients/{client_id}/messages")
async def client_messages(client_id: int, s: Db, query: Annotated[ClientMessageQuery, Query()]):
    if await s.get(Client, client_id) is None:
        raise HTTPException(404, "Client not found")
    return await message_page(s, query, client_id)


@router.get("/api/activity/llm", dependencies=[Depends(owner_only)])
async def llm(s: Db, query: Annotated[LlmQuery, Query()]):
    filters = query.filters(client_id=query.client_id, workflow=query.workflow,
                            model=query.model, provider=query.provider, has_error=query.has_error)
    rows, total = await ActivityRepo(s).llm(filters, query.limit, query.offset)
    return {"items": [llm_activity_out(*row) for row in rows], "total": total,
            "limit": query.limit, "offset": query.offset}
