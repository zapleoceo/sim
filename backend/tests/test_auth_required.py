"""Every API route needs a session, except an explicit list of public ones.

Walks the routes of the real app, so a new router that forgets `Authed`/`Owner`/`Superadmin`
fails here instead of shipping an open endpoint.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi.routing import APIRoute

from sim.api.main import create_app
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


def is_public(path: str) -> bool:
    return path in PUBLIC or path.startswith(PUBLIC_PREFIXES)


def protected_endpoints(routes: list) -> list[tuple[str, str]]:
    return sorted(
        (method, route.path)
        for route in routes if isinstance(route, APIRoute) and not is_public(route.path)
        for method in route.methods
        if (method, route.path) not in NO_SESSION_NEEDED
    )


@pytest.fixture
async def anonymous(container: Container) -> AsyncIterator[tuple[httpx.AsyncClient, list]]:
    app = create_app(container=container)
    app.state.container = container  # ASGITransport does not run lifespan
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, app.routes


async def test_every_non_public_route_without_a_session_is_401(anonymous):
    client, routes = anonymous
    endpoints = protected_endpoints(routes)
    assert len(endpoints) > 50   # the walk really sees the app's routers

    statuses = {}
    for method, path in endpoints:
        response = await client.request(method, PATH_PARAM.sub("1", path))
        statuses[f"{method} {path}"] = response.status_code
    assert {route: code for route, code in statuses.items() if code != 401} == {}


async def test_the_public_list_matches_real_routes(anonymous):
    """A renamed public route must not leave a stale entry that silently exempts nothing."""
    _, routes = anonymous
    paths = {route.path for route in routes if isinstance(route, APIRoute)}
    assert PUBLIC <= paths
    assert {path for _, path in NO_SESSION_NEEDED} <= paths
    assert any(path.startswith(PUBLIC_PREFIXES) for path in paths)
