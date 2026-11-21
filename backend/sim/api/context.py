"""What Litestar injects into route handlers: `ctx` (process-wide objects), `db` (a request
session inside one transaction) and `admin` (who is logged in, set by the auth guard).

`ApiContext` and `AdminContext` are plain classes on purpose: Litestar passes instances of
arbitrary classes through its signature validation unchanged."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from litestar import Request
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.channel_logins import ChannelLogins
from sim.api.live import EventHub
from sim.api.ratelimit import LoginRateLimiter
from sim.channels.whatsapp.receiver import WhatsAppReceiver
from sim.config import Settings
from sim.services.deps import Deps
from sim.services.inbox import Inbox

ADMIN_SCOPE_KEY = "sim_admin"
NOT_LOGGED_IN = "Нужно войти"


class ApiContext:
    """Ports and settings (`deps`), the live-update hub, the login limiter, the pending
    messenger account logins of this process (Telegram QR, Instagram, WhatsApp QR) and the
    WhatsApp webhook receiver."""

    def __init__(self, deps: Deps, *, hub: EventHub, limiter: LoginRateLimiter,
                 logins: ChannelLogins) -> None:
        self.deps = deps
        self.settings: Settings = deps.settings
        self.hub = hub
        self.limiter = limiter
        self.logins = logins
        self.whatsapp = WhatsAppReceiver(deps.sessions, deps.settings.secret_key, Inbox(deps),
                                         deps.clock, deps.tasks)


class AdminContext:
    """The admin of the current request and the CSRF token of their session."""

    def __init__(self, *, admin_id: int, name: str, username: str | None, csrf: str) -> None:
        self.admin_id = admin_id
        self.name = name
        self.username = username
        self.csrf = csrf

    @property
    def actor(self) -> str:
        """`chat_event.actor` of what this admin does."""
        return f"operator:{self.admin_id}"

    def view(self) -> dict[str, Any]:
        return {"id": self.admin_id, "name": self.name, "username": self.username}


async def provide_ctx(state: State) -> ApiContext:
    return state.ctx  # type: ignore[no-any-return]


async def provide_db(state: State) -> AsyncGenerator[AsyncSession, None]:
    """One session per request: committed when the handler returns, rolled back on error.
    A handler that must act after the commit (e.g. enqueue a send task) commits itself."""
    ctx: ApiContext = state.ctx
    async with ctx.deps.sessions() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def provide_admin(request: Request[Any, Any, Any]) -> AdminContext:
    admin = request.scope.get(ADMIN_SCOPE_KEY)  # type: ignore[call-overload]
    if not isinstance(admin, AdminContext):
        raise NotAuthorizedException(detail=NOT_LOGGED_IN)
    return admin
