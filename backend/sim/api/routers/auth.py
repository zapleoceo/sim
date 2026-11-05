from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from sim.api.clientip import resolve_client_ip
from sim.api.deps import COOKIE, App, Authed, Db
from sim.api.schemas import LoginIn, user_out
from sim.services.auth import AuthError, TooManyAttempts

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, c: App, s: Db):
    peer = request.client.host if request.client else ""
    addr = resolve_client_ip(peer, request.headers, c.settings.trusted_proxies)
    try:
        result = await c.auth(s).login(body.username, body.password, addr)
    except TooManyAttempts as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after_s)}
        ) from exc
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    response.set_cookie(
        COOKIE,
        result.token,
        max_age=c.settings.session_ttl_h * 3600,
        httponly=True,
        secure=c.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"user": user_out(result.user), "csrf_token": result.csrf_token}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, c: App, s: Db) -> Response:
    await c.auth(s).logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me")
async def me(p: Authed):
    return {"user": user_out(p.user), "csrf_token": p.session.csrf_token}
