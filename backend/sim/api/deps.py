"""FastAPI dependencies: unit of work, session auth, CSRF, roles."""
from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AdminUser, AuthSession
from sim.adapters.db.session import transaction
from sim.domain.enums import Role
from sim.wiring import Container

COOKIE = "sim_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_container(request: Request) -> Container:
    return request.app.state.container


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on error (scope=function: before the response is sent)."""
    async with transaction(get_container(request).factory) as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db, scope="function")]
App = Annotated[Container, Depends(get_container)]


@dataclass(frozen=True)
class Principal:
    user: AdminUser
    session: AuthSession


async def current_principal(request: Request, c: App, s: Db) -> Principal:
    row = await c.auth(s).authenticate(request.cookies.get(COOKIE))
    if row is None:
        raise HTTPException(401, "Not authenticated")
    return Principal(row.user, row)


async def authed(request: Request, p: Annotated[Principal, Depends(current_principal)]) -> Principal:
    """Logged in; mutating requests must also carry the session's CSRF token."""
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), p.session.csrf_token.encode()):
            raise HTTPException(403, "CSRF token missing or invalid")
    return p


async def owner_only(p: Annotated[Principal, Depends(authed)]) -> Principal:
    if not p.user.role.has_owner_rights:
        raise HTTPException(403, "Owner role required")
    return p


async def superadmin_only(p: Annotated[Principal, Depends(authed)]) -> Principal:
    if p.user.role is not Role.SUPERADMIN:
        raise HTTPException(403, "Superadmin role required")
    return p


Authed = Annotated[Principal, Depends(authed)]
Owner = Annotated[Principal, Depends(owner_only)]
Superadmin = Annotated[Principal, Depends(superadmin_only)]
