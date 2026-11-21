"""Dashboard login (docs/auth.md): Telegram Login Widget, server-side sessions, CSRF, guard."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers.base import BaseRouteHandler
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.context import ADMIN_SCOPE_KEY, NOT_LOGGED_IN, AdminContext, ApiContext
from sim.db.models import AdminUser, AuthSession

SESSION_COOKIE = "sim_session"
CSRF_HEADER = "X-CSRF-Token"
PUBLIC = "public"                       # route `opt` flag: no login needed
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WIDGET_MAX_AGE_S = 3600                 # auth_date older than this is rejected
WIDGET_MAX_SKEW_S = 60                  # auth_date this far in the future is tolerated

INVALID_LOGIN = "Не удалось проверить вход через Telegram"
ACCESS_DENIED = "Доступ не выдан"
CSRF_FAILED = "Неверный CSRF-токен"


class AuthError(Exception):
    """The widget payload is not signed by our bot, or is too old."""


class NotConfigured(Exception):
    """The login bot token is not set."""


@dataclass(frozen=True)
class TelegramProfile:
    id: int
    username: str | None
    name: str
    auth_date: int


def verify_telegram_widget(data: Mapping[str, Any], bot_token: str, *,
                           now: float | None = None) -> TelegramProfile:
    """Check a Telegram Login Widget payload: HMAC-SHA256 with secret = sha256(bot token) over
    the sorted `key=value` lines of every non-empty field except `hash`, compared in constant
    time; `auth_date` not older than an hour and not in the future."""
    if not bot_token:
        raise NotConfigured
    incoming = str(data.get("hash") or "").lower()
    pairs = sorted((k, str(v)) for k, v in data.items() if k != "hash" and v not in (None, ""))
    check_string = "\n".join(f"{k}={v}" for k, v in pairs)
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not incoming or not hmac.compare_digest(expected, incoming):
        raise AuthError(INVALID_LOGIN)
    try:
        auth_date = int(data["auth_date"])
        tg_id = int(data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError(INVALID_LOGIN) from exc
    now = time.time() if now is None else now
    if auth_date > now + WIDGET_MAX_SKEW_S or now - auth_date > WIDGET_MAX_AGE_S:
        raise AuthError(INVALID_LOGIN)
    first, last = str(data.get("first_name") or ""), str(data.get("last_name") or "")
    username = str(data.get("username") or "").lower() or None
    return TelegramProfile(
        id=tg_id, username=username, auth_date=auth_date,
        name=f"{first} {last}".strip() or (f"@{username}" if username else str(tg_id)),
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def find_login_admin(session: AsyncSession, profile: TelegramProfile,
                           superadmin_ids: list[int]) -> AdminUser | None:
    """The active admin for this Telegram account, refreshed from the profile. Ids listed in
    `SIM_SUPERADMIN_TELEGRAM_IDS` are created / re-activated on their first login."""
    admin = await session.scalar(select(AdminUser).where(AdminUser.telegram_id == profile.id))
    if profile.id in superadmin_ids:
        if admin is None:
            admin = AdminUser(telegram_id=profile.id)
            session.add(admin)
        admin.is_active = True
    if admin is None or not admin.is_active:
        return None
    admin.name = profile.name
    admin.username = profile.username
    await session.flush()
    return admin


async def open_session(session: AsyncSession, admin: AdminUser, *, now: datetime,
                       ttl: timedelta) -> tuple[str, str]:
    """A new server-side session: returns (cookie value, CSRF token). Only sha256 is stored."""
    await session.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    cookie_value, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    session.add(AuthSession(admin_id=admin.id, token_hash=hash_token(cookie_value),
                            csrf_token=csrf, expires_at=now + ttl))
    await session.flush()
    return cookie_value, csrf


async def authenticate(session: AsyncSession, cookie_value: str | None, *,
                       now: datetime) -> AdminContext | None:
    if not cookie_value:
        return None
    row = (await session.execute(
        select(AuthSession, AdminUser)
        .join(AdminUser, AdminUser.id == AuthSession.admin_id)
        .where(AuthSession.token_hash == hash_token(cookie_value))
    )).first()
    if row is None:
        return None
    auth, admin = row
    if auth.expires_at <= now or not admin.is_active:
        return None
    return AdminContext(admin_id=admin.id, name=admin.name, username=admin.username,
                        csrf=auth.csrf_token)


async def close_session(session: AsyncSession, cookie_value: str) -> None:
    await session.execute(delete(AuthSession).where(
        AuthSession.token_hash == hash_token(cookie_value)))


def check_csrf(connection: ASGIConnection[Any, Any, Any, Any], admin: AdminContext) -> None:
    """Every request that may change state must echo the session's CSRF token in a header."""
    if connection.scope.get("method", "GET") in SAFE_METHODS:  # type: ignore[call-overload]
        return
    sent = connection.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent, admin.csrf):
        raise PermissionDeniedException(detail=CSRF_FAILED)


async def require_admin(connection: ASGIConnection[Any, Any, Any, Any],
                        route_handler: BaseRouteHandler) -> None:
    """Litestar guard of the whole `/api` router. Routes with `opt={"public": True}` skip it."""
    if route_handler.opt.get(PUBLIC):
        return
    ctx: ApiContext = connection.app.state.ctx
    async with ctx.deps.sessions() as session:
        admin = await authenticate(session, connection.cookies.get(SESSION_COOKIE),
                                   now=ctx.deps.clock.now())
    if admin is None:
        raise NotAuthorizedException(detail=NOT_LOGGED_IN)
    check_csrf(connection, admin)
    connection.scope[ADMIN_SCOPE_KEY] = admin  # type: ignore[literal-required]
