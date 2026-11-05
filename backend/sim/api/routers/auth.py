from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from sim.api.clientip import resolve_client_ip
from sim.api.deps import COOKIE, App, Authed, Db, Principal, authed
from sim.api.schemas import TelegramLoginIn, user_out
from sim.config import Settings
from sim.services.auth import (
    ACCESS_DENIED,
    AccessDenied,
    AuthError,
    NotConfigured,
    TooManyAttempts,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def client_addr(request: Request, cfg: Settings) -> str:
    peer = request.client.host if request.client else ""
    return resolve_client_ip(peer, request.headers, cfg.trusted_proxies)


def set_session_cookie(response: Response, cfg: Settings, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=cfg.session_ttl_h * 3600,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",
    )


def too_many(exc: TooManyAttempts) -> HTTPException:
    return HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after_s)})


@router.get("/config")
async def auth_config(c: App):
    return {"bot_username": c.settings.tg_login_bot_username.lstrip("@")}


@router.post("/telegram")
async def login_telegram(body: TelegramLoginIn, request: Request, c: App, s: Db):
    addr = client_addr(request, c.settings)
    # model_dump keeps unknown extra fields: they are part of the signed string.
    data = body.model_dump(exclude_none=True)
    try:
        result = await c.auth(s).login_telegram(data, addr)
    except TooManyAttempts as exc:
        raise too_many(exc) from exc
    except NotConfigured as exc:
        raise HTTPException(503, "Telegram login is not configured") from exc
    except AuthError as exc:
        await s.commit()  # a replayed/rejected attempt must not roll back the recorded hash
        raise HTTPException(401, str(exc)) from exc
    except AccessDenied as exc:
        await s.commit()  # keep the one-time hash consumed
        return JSONResponse(
            {"detail": ACCESS_DENIED, "telegram_id": exc.telegram_id}, status_code=403
        )
    response = JSONResponse({"user": user_out(result.user), "csrf_token": result.csrf_token})
    set_session_cookie(response, c.settings, result.token)
    return response


@router.get("/link", include_in_schema=False)
async def login_link(token: str, request: Request, c: App, s: Db):
    """Break-glass: consumes a one-time link made by `python -m sim.cli login-link`."""
    try:
        result = await c.auth(s).login_with_link(token, client_addr(request, c.settings))
    except TooManyAttempts as exc:
        raise too_many(exc) from exc
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, c.settings, result.token)
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, c: App, s: Db) -> Response:
    token = request.cookies.get(COOKIE)
    # A live session needs the CSRF token like every other mutation (else a cross-site POST
    # could log the admin out); an expired/unknown one has nothing to protect: just clear the cookie.
    if (row := await c.auth(s).authenticate(token)) is not None:
        await authed(request, Principal(row.user, row))
        await c.auth(s).logout(token)
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me")
async def me(p: Authed):
    return {"user": user_out(p.user), "csrf_token": p.session.csrf_token}
