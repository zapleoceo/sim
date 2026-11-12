"""Dialogs (the unified inbox), clients and the funnel board."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.audio import AudioError
from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.locks import NS_DIALOG, lock
from sim.adapters.db.models import Client, Dialog, Message
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.conversations import (
    ClientRepo,
    DialogRepo,
    FunnelEventRepo,
    MessageRepo,
)
from sim.adapters.db.repositories.records import PartnerEventRepo
from sim.api.deps import App, Authed, Db, authed
from sim.api.schemas import (
    AiToggleIn,
    ClientPatchIn,
    OperatorMessageIn,
    StageChangeIn,
    client_mini,
    client_out,
    dialog_out,
    dialog_row,
    funnel_event_out,
    message_out,
    partner_event_out,
)
from sim.clock import to_naive_utc
from sim.domain.enums import ChannelKind, ClientStatus, Direction, SessionStatus, Stage
from sim.services.funnel import FunnelRuleError, FunnelService
from sim.services.outbound import cancel_queued_ai
from sim.services.voice import VOICE_NOTE

dialogs = APIRouter(prefix="/api/dialogs", tags=["dialogs"], dependencies=[Depends(authed)])
clients = APIRouter(prefix="/api/clients", tags=["clients"], dependencies=[Depends(authed)])
funnel = APIRouter(prefix="/api/funnel", tags=["funnel"], dependencies=[Depends(authed)])

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
FUNNEL_CLIENTS_PER_STAGE = 50
MAX_OPERATOR_AUDIO_BYTES = 8 * 1024 * 1024
ATTACHMENT_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt"}


async def dialog_or_404(s: Db, dialog_id: int) -> Dialog:
    dialog = await DialogRepo(s).get(dialog_id)
    if dialog is None:
        raise HTTPException(404, "Dialog not found")
    return dialog


async def client_or_404(s: Db, client_id: int) -> Client:
    client = await ClientRepo(s).get(client_id)
    if client is None:
        raise HTTPException(404, "Client not found")
    return client


def send_capabilities(c: App, dialog: Dialog) -> dict[str, bool]:
    """Available transport operations, not proof that a channel send will succeed."""
    result = {"text": False, "voice": False, "attachment": False}
    if dialog.channel.status is not SessionStatus.ACTIVE:
        return result
    connector = c.registry.get(dialog.channel.kind)
    if connector is None:
        return result
    try:
        port = connector.build_port(
            dialog.channel_id, c.box.decrypt_json(dialog.channel.session_enc)
        )
    except Exception:
        return result
    caps = getattr(port, "capabilities", frozenset())
    return {
        "text": True,
        "voice": Capability.SEND_VOICE in caps,
        "attachment": Capability.SEND_ATTACHMENT in caps,
    }


async def channels_of(s: AsyncSession, client: Client) -> list[dict[str, Any]]:
    return [
        {"kind": d.channel.kind.value, "dialog_id": d.id}
        for d in await DialogRepo(s).for_client(client.id)
    ]


# ---- dialogs ------------------------------------------------------------------------------
@dialogs.get("")
async def list_dialogs(  # noqa: PLR0913
    s: Db,
    channel_kind: ChannelKind | None = None,
    persona_id: int | None = None,
    stage: Stage | None = None,
    status: ClientStatus | None = None,
    avatar: str | None = None,
    q: str | None = None,
    unread_only: bool = False,
    needs_operator: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
):
    rows, total = await DialogRepo(s).search(
        limit, offset, channel_kind=channel_kind, persona_id=persona_id, stage=stage,
        status=status, avatar=avatar, q=q, unread_only=unread_only, needs_operator=needs_operator,
    )
    personas = {p.id: p for p in await PersonaRepo(s).all()}
    return {"items": [dialog_row(d, personas.get(d.client.persona_id)) for d in rows], "total": total}


@dialogs.get("/{dialog_id}")
async def get_dialog(dialog_id: int, s: Db, c: App):
    dialog = await dialog_or_404(s, dialog_id)
    messages = await MessageRepo(s).recent(dialog_id, 1000)
    return {
        "dialog": {**dialog_out(dialog), "send_capabilities": send_capabilities(c, dialog),
                   "channel_status": dialog.channel.status.value},
        "client": client_out(dialog.client, await channels_of(s, dialog.client)),
        "messages": [message_out(m) for m in messages],
    }


@dialogs.post("/{dialog_id}/messages", status_code=201)
async def send_operator_message(dialog_id: int, body: OperatorMessageIn, c: App, s: Db, p: Authed):
    await lock(s, NS_DIALOG, dialog_id)
    dialog = await dialog_or_404(s, dialog_id)
    if not send_capabilities(c, dialog)["text"]:
        raise HTTPException(409, "Channel is not connected")
    try:
        message = await c.outbound(s).send_manual(
            dialog, p.user.id, body.text, request_id=body.request_id
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return message_out(message)


@dialogs.post("/{dialog_id}/attachments", status_code=201)
async def send_operator_attachment(
    dialog_id: int, c: App, s: Db, p: Authed,
    file: Annotated[UploadFile, File()], caption: str = Form(default=""),
    request_id: str | None = Form(default=None),
):
    if request_id is not None and not (1 <= len(request_id) <= 64):
        raise HTTPException(422, "Invalid request id")
    if len(caption) > 1000:
        raise HTTPException(422, "Caption exceeds 1000 characters")
    dialog = await dialog_or_404(s, dialog_id)
    if dialog.channel.status is not SessionStatus.ACTIVE:
        raise HTTPException(409, "Channel is not connected")
    connector = c.registry.get(dialog.channel.kind)
    if connector is None:
        raise HTTPException(422, "This channel cannot send attachments")
    try:
        port = connector.build_port(
            dialog.channel_id, c.box.decrypt_json(dialog.channel.session_enc)
        )
    except Exception as exc:
        raise HTTPException(409, "Channel session is unavailable") from exc
    if Capability.SEND_ATTACHMENT not in getattr(port, "capabilities", frozenset()):
        raise HTTPException(422, "This channel cannot send attachments")
    if request_id:
        existing = await s.scalar(
            select(Message).where(Message.dialog_id == dialog_id,
                                  Message.client_request_id == request_id)
        )
        if existing is not None:
            if existing.author.value != "operator" or existing.media_kind not in ("image", "file"):
                raise HTTPException(409, "Request id belongs to another message")
            return message_out(existing)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ATTACHMENT_SUFFIXES:
        raise HTTPException(422, "Unsupported attachment type")
    data = await file.read(MAX_OPERATOR_AUDIO_BYTES + 1)
    await file.close()
    if not data or len(data) > MAX_OPERATOR_AUDIO_BYTES:
        raise HTTPException(413, "Attachment exceeds the 8 MiB limit")
    if suffix in {".jpg", ".jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(422, "Invalid JPEG")
    if suffix == ".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422, "Invalid PNG")
    if suffix == ".webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise HTTPException(422, "Invalid WebP")
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(422, "Invalid PDF")
    if suffix == ".txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Text attachment must be UTF-8") from exc
    name = c.media.save(data, suffix.lstrip("."))
    await lock(s, NS_DIALOG, dialog_id)
    dialog = await dialog_or_404(s, dialog_id)
    message = await c.outbound(s).send_manual(
        dialog, p.user.id, caption or "[вложение]", request_id=request_id,
        media_kind="image" if suffix in {".jpg", ".jpeg", ".png", ".webp"} else "file",
        media_url=f"/api/media/{name}",
    )
    return message_out(message)


@dialogs.post("/{dialog_id}/voice", status_code=201)
async def send_operator_voice(
    dialog_id: int, c: App, s: Db, p: Authed,
    file: Annotated[UploadFile, File()], request_id: str | None = Form(default=None),
):
    """Record/upload audio, convert to OGG/Opus, then queue one operator voice note."""
    if request_id is not None and not (1 <= len(request_id) <= 64):
        raise HTTPException(422, "Invalid request id")
    dialog = await dialog_or_404(s, dialog_id)
    if dialog.channel.status is not SessionStatus.ACTIVE:
        raise HTTPException(409, "Channel is not connected")
    connector = c.registry.get(dialog.channel.kind)
    if connector is None:
        raise HTTPException(422, "This channel cannot send voice")
    try:
        port = connector.build_port(
            dialog.channel_id, c.box.decrypt_json(dialog.channel.session_enc)
        )
    except Exception as exc:
        raise HTTPException(409, "Channel session is unavailable") from exc
    if Capability.SEND_VOICE not in getattr(port, "capabilities", frozenset()):
        raise HTTPException(422, "This channel cannot send voice")
    if request_id:
        existing = await s.scalar(
            select(Message).where(Message.dialog_id == dialog_id,
                                  Message.client_request_id == request_id)
        )
        if existing is not None:
            if existing.author.value != "operator" or existing.media_kind != VOICE_NOTE:
                raise HTTPException(409, "Request id belongs to another message")
            return message_out(existing)
    if not (file.content_type or "").startswith("audio/"):
        raise HTTPException(422, "An audio file is required")
    data = await file.read(MAX_OPERATOR_AUDIO_BYTES + 1)
    await file.close()
    if not data or len(data) > MAX_OPERATOR_AUDIO_BYTES:
        raise HTTPException(413, "Audio file exceeds the 8 MiB limit")
    try:
        audio = await c.encoder.to_voice_note(data)
    except AudioError as exc:
        raise HTTPException(422, "Audio conversion failed") from exc
    name = c.media.save(audio, "ogg")
    await lock(s, NS_DIALOG, dialog_id)
    dialog = await dialog_or_404(s, dialog_id)
    message = await c.outbound(s).send_manual(
        dialog, p.user.id, "[голосовое сообщение оператора]", request_id=request_id,
        media_kind=VOICE_NOTE, media_url=f"/api/media/{name}",
    )
    return message_out(message)


@dialogs.post("/{dialog_id}/ai")
async def toggle_ai(dialog_id: int, body: AiToggleIn, c: App, s: Db):
    await lock(s, NS_DIALOG, dialog_id)
    dialog = await dialog_or_404(s, dialog_id)
    dialog.ai_paused = body.paused
    if body.paused:
        await cancel_queued_ai(s, dialog.id, "cancelled: AI paused by an operator")
    else:
        dialog.needs_operator = False
        last = await MessageRepo(s).last(dialog.id)
        if last is not None and last.direction is Direction.IN:  # an unanswered client message
            await c.ingest(s).queue_reply(dialog, dialog.client)
    return dialog_out(dialog)


@dialogs.post("/{dialog_id}/resolve")
async def resolve_needs_operator(dialog_id: int, s: Db):
    """The operator dealt with it: clear the `needs_operator` flag without touching the AI
    pause (resuming the AI or sending a message also clears it)."""
    dialog = await dialog_or_404(s, dialog_id)
    dialog.needs_operator = False
    return dialog_out(dialog)


@dialogs.post("/{dialog_id}/read", status_code=204)
async def mark_read(dialog_id: int, s: Db) -> Response:
    (await dialog_or_404(s, dialog_id)).unread = 0
    return Response(status_code=204)


# ---- clients ------------------------------------------------------------------------------
@clients.get("")
async def list_clients(  # noqa: PLR0913
    s: Db,
    stage: Stage | None = None,
    status: ClientStatus | None = None,
    persona_id: int | None = None,
    q: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    rows, total = await ClientRepo(s).search(
        limit, offset, stage=stage, status=status, persona_id=persona_id, q=q
    )
    return {"items": [client_out(r, await channels_of(s, r)) for r in rows], "total": total}


@clients.get("/{client_id}")
async def get_client(client_id: int, s: Db):
    client = await client_or_404(s, client_id)
    return {
        **client_out(client, await channels_of(s, client)),
        "events": [funnel_event_out(e) for e in await FunnelEventRepo(s).for_client(client.id)],
        "partner_events": [
            partner_event_out(e) for e in await PartnerEventRepo(s).for_client(client.id)
        ],
    }


@clients.patch("/{client_id}")
async def patch_client(client_id: int, body: ClientPatchIn, s: Db):
    client = await client_or_404(s, client_id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "next_step_at":
            value = to_naive_utc(value) if value else None
        elif value is None:
            continue  # text fields are not nullable
        setattr(client, field, value)
    return client_out(client, await channels_of(s, client))


@clients.post("/{client_id}/stage")
async def change_stage(client_id: int, body: StageChangeIn, s: Db):
    client = await client_or_404(s, client_id)
    if body.stage is None and body.status is None:
        raise HTTPException(422, "stage or status is required")
    try:
        await FunnelService(s).operator_update(
            client, stage=body.stage, status=body.status, reason=body.reason
        )
    except FunnelRuleError as exc:
        raise HTTPException(422, str(exc)) from exc
    return client_out(client, await channels_of(s, client))


# ---- funnel -------------------------------------------------------------------------------
@funnel.get("")
async def funnel_board(s: Db, persona_id: int | None = None):
    rows = await ClientRepo(s).for_funnel(persona_id)
    stages = {st: {"stage": st.value, "count": 0, "clients": []} for st in Stage}
    statuses = {st.value: 0 for st in ClientStatus}
    for client in rows:
        column = stages[client.stage]
        column["count"] += 1
        if len(column["clients"]) < FUNNEL_CLIENTS_PER_STAGE:
            column["clients"].append(client_mini(client))
        statuses[client.status.value] += 1
    return {"stages": list(stages.values()), "statuses": statuses}
