"""`/api/auth/*`: Telegram login, current admin, logout (docs/auth.md)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from litestar import Request, Response, get, post
from litestar.exceptions import HTTPException, NotAuthorizedException, PermissionDeniedException
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api import views
from sim.api.auth import (
    ACCESS_DENIED,
    INVALID_LOGIN,
    PUBLIC,
    SESSION_COOKIE,
    AuthError,
    NotConfigured,
    authenticate,
    check_csrf,
    close_session,
    find_login_admin,
    open_session,
    verify_telegram_widget,
)
from sim.api.context import AdminContext, ApiContext
from sim.api.ratelimit import TooManyAttempts


@get("/auth/config", opt={PUBLIC: True})
async def auth_config(ctx: ApiContext) -> dict[str, Any]:
    return {"bot_username": ctx.settings.tg_login_bot_username.lstrip("@")}


@post("/auth/telegram", opt={PUBLIC: True}, status_code=200)
async def login_telegram(request: Request[Any, Any, Any], ctx: ApiContext, db: AsyncSession,
                         data: dict[str, Any]) -> Response[dict[str, Any]]:
    keys = ctx.limiter.keys_for(str(data.get("id", "")),
                                request.client.host if request.client else "")
    try:
        ctx.limiter.check(*keys)
    except TooManyAttempts as exc:
        raise HTTPException(detail=str(exc), status_code=429,
                            headers={"Retry-After": str(exc.retry_after_s)}) from exc
    now = ctx.deps.clock.now()
    try:
        profile = verify_telegram_widget(data, ctx.settings.tg_login_bot_token,
                                         now=now.timestamp())
    except NotConfigured as exc:
        raise HTTPException(detail="Вход через Telegram не настроен", status_code=503) from exc
    except AuthError as exc:
        ctx.limiter.record_failure(*keys)
        raise NotAuthorizedException(detail=INVALID_LOGIN) from exc
    admin = await find_login_admin(db, profile, ctx.settings.superadmin_ids)
    if admin is None:
        ctx.limiter.record_failure(*keys)
        raise PermissionDeniedException(detail=ACCESS_DENIED)
    ctx.limiter.reset(*keys)
    ttl = timedelta(hours=ctx.settings.session_ttl_h)
    cookie_value, csrf = await open_session(db, admin, now=now, ttl=ttl)
    response = Response({"admin": views.admin_view(admin), "csrf": csrf})
    response.set_cookie(SESSION_COOKIE, cookie_value, max_age=int(ttl.total_seconds()),
                        path="/", secure=ctx.settings.cookie_secure, httponly=True,
                        samesite="lax")
    return response


@get("/auth/me")
async def me(admin: AdminContext) -> dict[str, Any]:
    return {"admin": admin.view(), "csrf": admin.csrf}


@post("/auth/logout", opt={PUBLIC: True}, status_code=204)
async def logout(request: Request[Any, Any, Any], ctx: ApiContext,
                 db: AsyncSession) -> Response[None]:
    """A live session needs the CSRF token like any mutation (else a cross-site POST could log
    the admin out); an expired or unknown cookie has nothing to protect and is just cleared."""
    cookie_value = request.cookies.get(SESSION_COOKIE)
    admin = await authenticate(db, cookie_value, now=ctx.deps.clock.now())
    if admin is not None and cookie_value:
        check_csrf(request, admin)
        await close_session(db, cookie_value)
    response: Response[None] = Response(None, status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


ROUTES = [auth_config, login_telegram, me, logout]
