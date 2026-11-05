"""Inbound webhooks: channels (Telegram, Evolution) and the partner."""
from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from sim.adapters.db.repositories.catalog import ChannelRepo
from sim.api.deps import App, Db
from sim.api.schemas import PartnerHookIn
from sim.domain.enums import ChannelKind, PartnerEventKind
from sim.services.admin_bot import SECRET_HEADER, admin_bot_secret
from sim.services.partner import (
    PartnerAuthError,
    PartnerDataError,
    PartnerEventIn,
    verify_signature,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hooks", tags=["hooks"])


async def channel_webhook(
    kind: ChannelKind, channel_id: int, request: Request, c: App, s: Db
) -> dict[str, Any]:
    channel = await ChannelRepo(s).get(channel_id)
    if channel is None or channel.kind is not kind or channel.is_archived:
        raise HTTPException(404, "Unknown channel")
    connector = c.registry.get(kind)
    if connector is None:
        raise HTTPException(404, "Channel kind is not available")
    session = c.box.decrypt_json(channel.session_enc)
    if not connector.verify_webhook(request.headers, session):
        raise HTTPException(401, "Bad webhook credentials")
    try:
        payload = await request.json()
        inbound = connector.parse_webhook(payload)
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("unparsable %s webhook for channel %s", kind.value, channel_id)
        return {"ok": True, "ignored": True, "reason": type(exc).__name__}
    ingest = c.ingest(s)
    for msg in inbound:
        await ingest.ingest(channel, msg)
    return {"ok": True, "messages": len(inbound)}


@router.post("/telegram/{channel_id}")
async def telegram_hook(channel_id: int, request: Request, c: App, s: Db):
    return await channel_webhook(ChannelKind.TELEGRAM, channel_id, request, c, s)


@router.post("/evolution/{channel_id}")
async def evolution_hook(channel_id: int, request: Request, c: App, s: Db):
    return await channel_webhook(ChannelKind.WHATSAPP, channel_id, request, c, s)


@router.post("/admin-bot")
async def admin_bot_hook(request: Request, c: App, s: Db):
    """Updates of the login bot (admin simulation chat). Always 200 once the secret matches:
    a non-2xx answer makes Telegram redeliver the same update in a loop."""
    token = c.settings.tg_login_bot_token
    if not token:
        raise HTTPException(404, "Not found")
    expected = admin_bot_secret(c.settings.secret_key, token)
    if not hmac.compare_digest(request.headers.get(SECRET_HEADER, "").encode(), expected.encode()):
        raise HTTPException(401, "Bad webhook credentials")
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True, "ignored": True}
    if isinstance(update, dict):
        try:
            await c.admin_bot(s).handle(update)
        except Exception as exc:  # never the exception text: a Bot API URL carries the token
            log.warning("admin bot update failed: %s", type(exc).__name__)
    return {"ok": True}


@router.post("/partner")
async def partner_hook(request: Request, c: App, s: Db):
    body = await request.body()
    try:
        verify_signature(
            c.settings.partner_webhook_secret,
            request.headers.get("X-Sim-Timestamp"),
            request.headers.get("X-Sim-Signature"),
            body,
        )
    except PartnerAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    try:
        data = PartnerHookIn.model_validate(json.loads(body))
        await c.partner(s).record(
            PartnerEventIn(
                client_ref=data.client_ref,
                kind=PartnerEventKind(data.event),
                external_id=data.external_id,
                occurred_at=data.occurred_at,
                amount=data.amount,
                currency=data.currency,
                payout=data.payout,
            )
        )
    except (ValueError, ValidationError) as exc:  # PartnerDataError is a ValueError
        status = 404 if isinstance(exc, PartnerDataError) else 422
        raise HTTPException(status, str(exc)[:300]) from exc
    return {"ok": True}
