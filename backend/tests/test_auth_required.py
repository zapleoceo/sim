"""Every API route needs a session, except an explicit list of public ones.

Walks the routes of every router the app includes (`sim.api.main.ROUTERS`), so a new route that
forgets `Authed`/`Owner`/`Superadmin` fails here instead of shipping an open endpoint.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi.routing import APIRoute

from sim.api.main import ROUTERS, create_app
from sim.wiring import Container

PUBLIC = frozenset({
    "/api/live", "/api/ready", "/api/health",
    "/api/auth/config", "/api/auth/telegram", "/api/auth/link",
})
PUBLIC_PREFIXES = ("/hooks/",)   # webhooks authenticate by their own secret, not by a session
# Logout without a session only clears the cookie and answers 204 (see routers/auth.py):
# there is nothing to protect, and a stale cookie must still be removable.
NO_SESSION_NEEDED = frozenset({("POST", "/api/auth/logout")})
PATH_PARAM = re.compile(r"\{[^}]+\}")


def api_routes() -> list[APIRoute]:
    return [route for router in ROUTERS for route in router.routes if isinstance(route, APIRoute)]


def is_public(path: str) -> bool:
    return path in PUBLIC or path.startswith(PUBLIC_PREFIXES)


def protected_endpoints() -> list[tuple[str, str]]:
    return sorted(
        (method, route.path)
        for route in api_routes() if not is_public(route.path)
        for method in route.methods
        if (method, route.path) not in NO_SESSION_NEEDED
    )


@pytest.fixture
async def anonymous(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container=container)
    app.state.container = container  # ASGITransport does not run lifespan
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_every_non_public_route_without_a_session_is_401(anonymous):
    endpoints = protected_endpoints()
    assert len(endpoints) > 50   # the walk really sees the app's routers

    statuses = {}
    for method, path in endpoints:
        response = await anonymous.request(method, PATH_PARAM.sub("1", path))
        statuses[f"{method} {path}"] = response.status_code
    assert {route: code for route, code in statuses.items() if code != 401} == {}


async def test_the_public_list_matches_real_routes(anonymous):
    """A renamed public route must not leave a stale entry that silently exempts nothing,
    and the walked routers are the ones the app serves."""
    paths = {route.path for route in api_routes()}
    assert PUBLIC <= paths
    assert {path for _, path in NO_SESSION_NEEDED} <= paths
    assert any(path.startswith(PUBLIC_PREFIXES) for path in paths)
    assert (await anonymous.get("/api/live")).status_code == 200
