from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from sim.adapters.db.models import Channel
from sim.adapters.db.repositories.catalog import ChannelRepo
from sim.api.deps import App, Db, authed, owner_only
from sim.api.schemas import ChannelIn, VerifyIn, channel_out, connect_state_out

# ChannelError becomes 422 in api/errors.py.

# Reading channels is for everyone logged in (the inbox filters by them); changes are owner-only.
router = APIRouter(prefix="/api/channels", tags=["channels"])


async def channel_or_404(s: Db, channel_id: int) -> Channel:
    channel = await ChannelRepo(s).get(channel_id)
    if channel is None or channel.is_archived:
        raise HTTPException(404, "Channel not found")
    return channel


@router.get("", dependencies=[Depends(authed)])
async def list_channels(c: App, s: Db):
    svc = c.channels(s)
    return {"items": [channel_out(ch, saved_credentials=svc.has_saved_credentials(ch))
                      for ch in await svc.list()]}


@router.post("", status_code=201, dependencies=[Depends(owner_only)])
async def create_channel(body: ChannelIn, c: App, s: Db):
    return channel_out(await c.channels(s).create(body.kind, body.persona_id, body.name))


@router.delete("/{channel_id}", status_code=204, dependencies=[Depends(owner_only)])
async def delete_channel(channel_id: int, c: App, s: Db) -> Response:
    await c.channels(s).remove(await channel_or_404(s, channel_id))
    return Response(status_code=204)


@router.post("/{channel_id}/connect", dependencies=[Depends(owner_only)])
async def connect_channel(channel_id: int, credentials: dict[str, str], c: App, s: Db):
    channel = await channel_or_404(s, channel_id)
    return connect_state_out(await c.channels(s).connect(channel, credentials))


@router.post("/{channel_id}/verify", dependencies=[Depends(owner_only)])
async def verify_channel(channel_id: int, body: VerifyIn, c: App, s: Db):
    channel = await channel_or_404(s, channel_id)
    return connect_state_out(await c.channels(s).verify(channel, body.code))


@router.get("/{channel_id}/state", dependencies=[Depends(owner_only)])
async def channel_state(channel_id: int, c: App, s: Db):
    channel = await channel_or_404(s, channel_id)
    return connect_state_out(await c.channels(s).state(channel))


@router.get("/{channel_id}/credentials", dependencies=[Depends(owner_only)])
async def channel_credentials(channel_id: int, c: App, s: Db, response: Response):
    """For the reconnect form: `{fields, saved_secrets}`. Non-secret fields (api_id, phone, login)
    come back; secrets (token, password, api_hash, proxy) only by name. Owner-only, `no-store`."""
    channel = await channel_or_404(s, channel_id)
    response.headers["Cache-Control"] = "no-store"
    return c.channels(s).saved_public(channel)
