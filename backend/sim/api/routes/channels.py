"""`/api/personas/{id}/channels*`: a persona's messenger accounts and connecting them from the
dashboard (docs/personas.md, «Канал»; docs/channels.md; docs/api.md).

One channel per kind per persona (Telegram, Instagram, WhatsApp), created on the first login.
Each kind starts with its own form (`POST .../channels/{telegram|instagram|whatsapp}/login`);
then every kind is driven the same way through its login flow (sim/api/channel_logins.py):
`GET .../channels/{cid}/login` (poll), `POST .../login/code` (an Instagram code; empty =
«Повторить»), `POST .../login/password` (Telegram 2FA), `DELETE .../login` (cancel).

A finished login goes into `channel.credentials_enc` (Fernet JSON) and the channel becomes
`connected`; the Telegram process / the worker pick it up by themselves. A WhatsApp pairing
stores its instance and webhook secret at once (status `pending`): Evolution's events must be
verifiable before the scan. Responses never contain credentials, sessions, passwords or codes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from litestar import delete, get, post
from litestar.exceptions import ClientException, NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.queries import persona_or_404
from sim.api.schemas import (
    CodeIn,
    InstagramLoginIn,
    PasswordIn,
    TelegramLoginIn,
    WhatsAppLoginIn,
)
from sim.channels.instagram.login import IgLoginRequest
from sim.channels.login import LoginResult, LoginState
from sim.crypto import CryptoError, decrypt_json, encrypt_json
from sim.db.models import Channel, Persona
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.telegram.client import account_label

Json = dict[str, Any]
Begin = Callable[[Channel, Persona], Awaitable[LoginResult]]
# A login in progress (or failed) never touches a channel that works or was switched off.
SETTLING = frozenset({ChannelStatus.NEW, ChannelStatus.PENDING, ChannelStatus.ERROR,
                      ChannelStatus.CHALLENGE})


async def own_channel(db: AsyncSession, persona_id: int, channel_id: int) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.persona_id != persona_id:
        raise NotFoundException(detail=f"Канал не найден: {channel_id}")
    return channel


async def channel_of_kind(db: AsyncSession, persona: Persona, kind: ChannelKind) -> Channel:
    """The persona's channel of this messenger; a new one (status `new`) the first time."""
    channel = await db.scalar(
        select(Channel).where(Channel.persona_id == persona.id, Channel.kind == kind)
        .order_by(Channel.id).limit(1))
    if channel is None:
        channel = Channel(persona_id=persona.id, kind=kind, title=persona.name,
                          status=ChannelStatus.NEW)
        db.add(channel)
        await db.flush()
    return channel


async def account_owner(db: AsyncSession, channel: Channel, account_id: str) -> str | None:
    """The persona that already works through this account on another channel of the kind."""
    return await db.scalar(
        select(Persona.name).join(Channel, Channel.persona_id == Persona.id)
        .where(Channel.account_id == account_id, Channel.kind == channel.kind,
               Channel.id != channel.id, Channel.status != ChannelStatus.DISABLED))


def credentials_of(channel: Channel, ctx: ApiContext) -> dict[str, Any] | None:
    if not channel.credentials_enc:
        return None
    try:
        return decrypt_json(channel.credentials_enc, ctx.settings.secret_key)
    except CryptoError:
        return None


def store_credentials(channel: Channel, ctx: ApiContext, credentials: dict[str, Any]) -> None:
    try:
        channel.credentials_enc = encrypt_json(credentials, ctx.settings.secret_key)
    except CryptoError as exc:
        raise ClientException(detail="На сервере не задан ключ шифрования "
                                     "SIM_SECRET_KEY") from exc


async def settle(result: LoginResult, channel: Channel, ctx: ApiContext,
                 db: AsyncSession) -> Json:
    """Store what a login step brought; the response shape of every login step."""
    if result.state == LoginState.CONNECTED and result.credentials is not None:
        account_id = result.account_id or str(getattr(result.me, "id", "") or "") or None
        owner = await account_owner(db, channel, account_id) if account_id else None
        if owner is not None:
            result = LoginResult(LoginState.ERROR,
                                 f"Этот аккаунт уже подключён к персонажу «{owner}»")
        else:
            store_credentials(channel, ctx, result.credentials)
            channel.status = ChannelStatus.CONNECTED
            channel.account_id = account_id
            label = result.account_label
            if not label and result.me is not None:
                label = account_label(result.me)                # Telegram: @username / +phone
            channel.account_label = label or channel.account_label
            channel.last_error = None
    elif result.credentials is not None:
        store_credentials(channel, ctx, result.credentials)    # WhatsApp: instance + secret
    over = result.state in (LoginState.ERROR, LoginState.NONE)
    if over and channel.status == ChannelStatus.PENDING:
        channel.status = ChannelStatus.NEW
    elif not over and result.state != LoginState.CONNECTED and channel.status in SETTLING:
        channel.status = ChannelStatus.PENDING
    await db.flush()
    return {"channel": views.channel_view(channel), "login": result.view()}


async def start(kind: ChannelKind, persona_id: int, ctx: ApiContext, db: AsyncSession,
                begin: Begin) -> Json:
    """The shared part of every «Подключить»: the persona's channel of the kind, then the
    kind's first step `begin(channel)`."""
    persona = await persona_or_404(db, persona_id)
    channel = await channel_of_kind(db, persona, kind)
    return await settle(await begin(channel, persona), channel, ctx, db)


@get("/personas/{persona_id:int}/channels")
async def list_channels(persona_id: int, ctx: ApiContext, db: AsyncSession) -> Json:
    """The persona's channels and what «Подключить» can offer on this server."""
    await persona_or_404(db, persona_id)
    channels = await db.scalars(select(Channel).where(Channel.persona_id == persona_id)
                                .order_by(Channel.id))
    return {"items": [views.channel_view(c) for c in channels],
            "kinds": [info.view() for info in ctx.logins.kinds()]}


@post("/personas/{persona_id:int}/channels/telegram/login", status_code=200)
async def start_telegram(persona_id: int, data: TelegramLoginIn, ctx: ApiContext,
                         db: AsyncSession) -> Json:
    """A QR login of the persona's Telegram account (api_id / api_hash, optional proxy)."""
    async def begin(channel: Channel, _persona: Persona) -> LoginResult:
        return await ctx.logins.telegram.start(channel.id, data.api_id, data.api_hash.strip(),
                                               data.proxy.strip())

    return await start(ChannelKind.TELEGRAM_USER, persona_id, ctx, db, begin)


@post("/personas/{persona_id:int}/channels/instagram/login", status_code=200)
async def start_instagram(persona_id: int, data: InstagramLoginIn, ctx: ApiContext,
                          db: AsyncSession) -> Json:
    """Log the persona's Instagram account in: password, sessionid or a settings JSON."""
    async def begin(channel: Channel, persona: Persona) -> LoginResult:
        # The device's language and time zone are hers (aligned with the proxy's country).
        request = IgLoginRequest(
            proxy=data.proxy.strip(), username=data.username.strip().lstrip("@"),
            password=data.password, sessionid=data.sessionid, settings_json=data.settings_json,
            lang=persona.language or "",
            tz_offset_s=utc_offset_s(persona.timezone, ctx.deps.clock.now()))
        return await ctx.logins.instagram.start(channel.id, request)

    return await start(ChannelKind.INSTAGRAM, persona_id, ctx, db, begin)


@post("/personas/{persona_id:int}/channels/whatsapp/login", status_code=200)
async def start_whatsapp(persona_id: int, data: WhatsAppLoginIn, ctx: ApiContext,
                         db: AsyncSession) -> Json:
    """Link the persona's WhatsApp by QR (Evolution API instance `sim-<channel id>`)."""
    async def begin(channel: Channel, _persona: Persona) -> LoginResult:
        if data.phone.strip() and not channel.account_label:
            channel.account_label = data.phone.strip()
        return await ctx.logins.whatsapp.start(channel.id, credentials_of(channel, ctx))

    return await start(ChannelKind.WHATSAPP, persona_id, ctx, db, begin)


@get("/personas/{persona_id:int}/channels/{channel_id:int}/login")
async def login_state(persona_id: int, channel_id: int, ctx: ApiContext,
                      db: AsyncSession) -> Json:
    """Polled by the dashboard: a fresh QR, the next question, connected, or an error."""
    channel = await own_channel(db, persona_id, channel_id)
    flow = ctx.logins.flow(channel.kind)
    return await settle(await flow.poll(channel.id, credentials_of(channel, ctx)), channel,
                        ctx, db)


@post("/personas/{persona_id:int}/channels/{channel_id:int}/login/code", status_code=200)
async def login_code(persona_id: int, channel_id: int, data: CodeIn, ctx: ApiContext,
                     db: AsyncSession) -> Json:
    """An Instagram 2FA / challenge code; empty = «Повторить» after approving in the app."""
    channel = await own_channel(db, persona_id, channel_id)
    flow = ctx.logins.flow(channel.kind)
    result = await flow.answer(channel.id, credentials_of(channel, ctx), data.code)
    return await settle(result, channel, ctx, db)


@post("/personas/{persona_id:int}/channels/{channel_id:int}/login/password", status_code=200)
async def login_password(persona_id: int, channel_id: int, data: PasswordIn, ctx: ApiContext,
                         db: AsyncSession) -> Json:
    """Telegram's 2FA (cloud) password."""
    channel = await own_channel(db, persona_id, channel_id)
    flow = ctx.logins.flow(channel.kind)
    result = await flow.answer(channel.id, credentials_of(channel, ctx), data.password)
    return await settle(result, channel, ctx, db)


@delete("/personas/{persona_id:int}/channels/{channel_id:int}/login", status_code=204)
async def cancel_login(persona_id: int, channel_id: int, ctx: ApiContext,
                       db: AsyncSession) -> None:
    """Drop the login in progress. A WhatsApp pairing that never connected also unlinks and
    deletes its Evolution instance."""
    channel = await own_channel(db, persona_id, channel_id)
    pairing = channel.status == ChannelStatus.PENDING
    credentials = credentials_of(channel, ctx) if pairing else None
    await ctx.logins.flow(channel.kind).cancel(channel.id, credentials)
    if pairing:
        channel.status = ChannelStatus.NEW
        if channel.kind == ChannelKind.WHATSAPP:
            channel.credentials_enc = None


@post("/personas/{persona_id:int}/channels/{channel_id:int}/disable", status_code=200)
async def disable_channel(persona_id: int, channel_id: int, ctx: ApiContext,
                          db: AsyncSession) -> Json:
    """«Отключить»: the messenger process lets the account go (Telegram ≤ 60 s; Instagram is
    not polled, sends fail; WhatsApp events are ignored). The session stays stored, so a new
    login brings it back."""
    channel = await own_channel(db, persona_id, channel_id)
    if channel.kind != ChannelKind.WHATSAPP:
        await ctx.logins.flow(channel.kind).cancel(channel.id, None)
    channel.status = ChannelStatus.DISABLED
    await db.flush()
    return views.channel_view(channel)


def utc_offset_s(timezone: str | None, now: datetime) -> int | None:
    """The persona's UTC offset at `now`, for the device time zone Instagram sees."""
    try:
        offset = now.astimezone(ZoneInfo(timezone or "UTC")).utcoffset()
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return int(offset.total_seconds()) if offset is not None else None


ROUTES = [list_channels, start_telegram, start_instagram, start_whatsapp, login_state,
          login_code, login_password, cancel_login, disable_channel]
