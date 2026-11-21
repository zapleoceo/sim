"""The Litestar application of the `api` process. Built from wired `Deps` (sim.services.deps),
so tests pass fakes of every port and the same code runs in production."""
from __future__ import annotations

from litestar import Litestar, Router
from litestar.datastructures import State
from litestar.di import Provide

from sim.api.auth import require_admin
from sim.api.channel_logins import ChannelLogins
from sim.api.context import ApiContext, provide_admin, provide_ctx, provide_db
from sim.api.errors import EXCEPTION_HANDLERS
from sim.api.live import EventHub, pg_listener
from sim.api.ratelimit import LoginRateLimiter
from sim.api.routes import API_ROUTES, system
from sim.channels.instagram.login import InstagramLogins
from sim.channels.whatsapp.login import WhatsAppLogins
from sim.services.deps import Deps
from sim.telegram.login import TelegramLogins

# Library videos and operator uploads go through the API; the default limit is 10 MB.
MAX_REQUEST_BYTES = 200 * 1024 * 1024


def create_app(deps: Deps, *, logins: TelegramLogins | None = None,
               instagram: InstagramLogins | None = None,
               whatsapp: WhatsAppLogins | None = None) -> Litestar:
    """`logins` / `instagram` / `whatsapp`: the account logins (fakes in tests)."""
    settings = deps.settings
    hub = EventHub(pg_listener(settings.database_url))
    channel_logins = ChannelLogins.from_settings(settings, telegram=logins, instagram=instagram,
                                                 whatsapp=whatsapp)
    ctx = ApiContext(deps, hub=hub, limiter=LoginRateLimiter(), logins=channel_logins)
    api = Router(path="/api", route_handlers=API_ROUTES, guards=[require_admin])
    return Litestar(
        route_handlers=[api, *system.ROUTES, *system.static_routes(settings.static_dir)],
        dependencies={
            "ctx": Provide(provide_ctx),
            "db": Provide(provide_db),
            "admin": Provide(provide_admin),
        },
        state=State({"ctx": ctx}),
        exception_handlers=EXCEPTION_HANDLERS,
        on_shutdown=[hub.close, channel_logins.close_all],
        openapi_config=None,
        request_max_body_size=MAX_REQUEST_BYTES,
    )
