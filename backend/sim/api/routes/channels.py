"""`/api/personas/{id}/channels*`: a persona's messenger accounts and connecting a Telegram
account by QR from the dashboard (docs/personas.md, «Канал»; docs/api.md).

The login itself runs in this process (sim/telegram/login.TelegramLogins). On success the
account goes into `channel.credentials_enc` (Fernet JSON {api_id, api_hash, session, proxy}),
the channel becomes `connected`, and the telegram process picks it up at its next refresh
(≤ 60 s). Responses never contain api_hash, the session or the password."""
from __future__ import annotations

from typing import Any

from litestar import delete, get, post
from litestar.exceptions import ClientException, NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.context import ApiContext
from sim.api.queries import persona_or_404
from sim.api.schemas import PasswordIn, TelegramLoginIn
from sim.crypto import CryptoError, encrypt_json
from sim.db.models import Channel, Persona
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.telegram.client import account_label
from sim.telegram.login import LoginResult, LoginState

Json = dict[str, Any]


async def own_channel(db: AsyncSession, persona_id: int, channel_id: int) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.persona_id != persona_id:
        raise NotFoundException(detail=f"Канал не найден: {channel_id}")
    return channel


async def telegram_channel(db: AsyncSession, persona: Persona) -> Channel:
    """The persona's Telegram account channel; a new one (status `new`) the first time."""
    channel = await db.scalar(
        select(Channel).where(Channel.persona_id == persona.id,
                              Channel.kind == ChannelKind.TELEGRAM_USER)
        .order_by(Channel.id).limit(1))
    if channel is None:
        channel = Channel(persona_id=persona.id, kind=ChannelKind.TELEGRAM_USER,
                          title=persona.name, status=ChannelStatus.NEW)
        db.add(channel)
        await db.flush()
    return channel


async def account_owner(db: AsyncSession, channel: Channel, account_id: str) -> str | None:
    """The persona that already works through this Telegram account on another channel."""
    return await db.scalar(
        select(Persona.name).join(Channel, Channel.persona_id == Persona.id)
        .where(Channel.account_id == account_id, Channel.id != channel.id,
               Channel.status != ChannelStatus.DISABLED))


async def settle(result: LoginResult, channel: Channel, ctx: ApiContext,
                 db: AsyncSession) -> Json:
    """Store a finished login on the channel; the response shape of every login step."""
    if result.state == LoginState.CONNECTED and result.credentials is not None:
        account_id = str(getattr(result.me, "id", "") or "") or None
        owner = await account_owner(db, channel, account_id) if account_id else None
        if owner is not None:
            result = LoginResult(LoginState.ERROR,
                                 f"Этот аккаунт уже подключён к персонажу «{owner}»")
        else:
            try:
                channel.credentials_enc = encrypt_json(result.credentials,
                                                       ctx.settings.secret_key)
            except CryptoError as exc:
                raise ClientException(detail="На сервере не задан ключ шифрования "
                                             "SIM_SECRET_KEY") from exc
            channel.status = ChannelStatus.CONNECTED
            channel.account_id = account_id
            channel.account_label = account_label(result.me)
            channel.last_error = None
            await db.flush()
    return {"channel": views.channel_view(channel), "login": result.view()}


@get("/personas/{persona_id:int}/channels")
async def list_channels(persona_id: int, db: AsyncSession) -> Json:
    await persona_or_404(db, persona_id)
    channels = await db.scalars(select(Channel).where(Channel.persona_id == persona_id)
                                .order_by(Channel.id))
    return {"items": [views.channel_view(c) for c in channels]}


@post("/personas/{persona_id:int}/channels/telegram/login", status_code=200)
async def start_login(persona_id: int, data: TelegramLoginIn, ctx: ApiContext,
                      db: AsyncSession) -> Json:
    """Start a QR login for the persona's Telegram channel (created on first use)."""
    persona = await persona_or_404(db, persona_id)
    channel = await telegram_channel(db, persona)
    result = await ctx.logins.start(channel.id, data.api_id, data.api_hash.strip(),
                                    data.proxy.strip())
    return await settle(result, channel, ctx, db)


@get("/personas/{persona_id:int}/channels/{channel_id:int}/login")
async def login_state(persona_id: int, channel_id: int, ctx: ApiContext,
                      db: AsyncSession) -> Json:
    """Polled by the dashboard: a fresh QR, the 2FA step, connected, or an error."""
    channel = await own_channel(db, persona_id, channel_id)
    return await settle(await ctx.logins.poll(channel.id), channel, ctx, db)


@post("/personas/{persona_id:int}/channels/{channel_id:int}/login/password", status_code=200)
async def login_password(persona_id: int, channel_id: int, data: PasswordIn, ctx: ApiContext,
                         db: AsyncSession) -> Json:
    channel = await own_channel(db, persona_id, channel_id)
    return await settle(await ctx.logins.password(channel.id, data.password), channel, ctx, db)


@delete("/personas/{persona_id:int}/channels/{channel_id:int}/login", status_code=204)
async def cancel_login(persona_id: int, channel_id: int, ctx: ApiContext,
                       db: AsyncSession) -> None:
    channel = await own_channel(db, persona_id, channel_id)
    await ctx.logins.cancel(channel.id)


@post("/personas/{persona_id:int}/channels/{channel_id:int}/disable", status_code=200)
async def disable_channel(persona_id: int, channel_id: int, ctx: ApiContext,
                          db: AsyncSession) -> Json:
    """«Отключить»: the telegram process lets the account go at its next refresh. The session
    stays stored, so a new QR login (or re-enabling by a login) brings it back."""
    channel = await own_channel(db, persona_id, channel_id)
    await ctx.logins.cancel(channel.id)
    channel.status = ChannelStatus.DISABLED
    await db.flush()
    return views.channel_view(channel)


ROUTES = [list_channels, start_login, login_state, login_password, cancel_login,
          disable_channel]
