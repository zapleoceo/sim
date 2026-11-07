"""Offline source-contract checks, not nginx syntax or runtime traffic validation."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sim.api.routers.auth import login_link, login_telegram, router

CONFIG = Path(__file__).resolve().parents[2] / "infra" / "nginx-sim.conf"
LOGIN_PATHS = ("/api/auth/telegram", "/api/auth/link")


@pytest.fixture
def nginx_config():
    # The repository config uses flat location blocks. Fail rather than silently ignore
    # a nested/new location form: review these contract checks if that layout changes.
    source = re.sub(r"#[^\n]*", "", CONFIG.read_text(encoding="utf-8"))
    blocks = re.findall(r"\blocation\s+([^{}]+)\{([^{}]*)\}", source)
    assert len(blocks) == len(re.findall(r"\blocation\s+", source))
    locations = {}
    for selector, body in blocks:
        selector = " ".join(selector.split())
        assert selector not in locations
        assert body.rstrip().endswith(";")
        locations[selector] = [
            " ".join(directive.split()) for directive in body.split(";") if directive.strip()
        ]
    return source, locations


def test_login_limit_only_targets_registered_login_routes(nginx_config):
    _, locations = nginx_config
    login_routes = {
        (route.path, frozenset(route.methods))
        for route in router.routes
        if route.endpoint in (login_telegram, login_link)
    }
    assert login_routes == {
        ("/api/auth/telegram", frozenset({"POST"})),
        ("/api/auth/link", frozenset({"GET"})),
    }
    protected = {
        selector
        for selector, directives in locations.items()
        if any(d.startswith("limit_req zone=sim_login ") for d in directives)
    }
    assert protected == {f"= {path}" for path, _ in login_routes}


def test_other_auth_and_read_routes_keep_the_generic_limit(nginx_config):
    _, locations = nginx_config
    # Exact matches keep config/me/logout, UI assets and other APIs out of the smaller
    # login bucket. No broad auth prefix/regex (or obsolete /auth/login) may shadow them.
    assert set(locations) == {"/", "/hooks/evolution/", *(f"= {p}" for p in LOGIN_PATHS)}
    assert "limit_req zone=sim_api burst=120 nodelay" in locations["/"]
    other_auth_paths = {route.path for route in router.routes} - set(LOGIN_PATHS)
    assert {"/api/auth/config", "/api/auth/me", "/api/auth/logout"} <= other_auth_paths
    assert not any(f"= {path}" in locations for path in other_auth_paths)


@pytest.mark.parametrize("path", LOGIN_PATHS)
def test_login_proxy_preserves_headers_upstream_and_timeout(nginx_config, path):
    _, locations = nginx_config
    directives = locations[f"= {path}"]
    assert "limit_req zone=sim_login burst=5 nodelay" in directives
    proxy_directives = [d for d in directives if not d.startswith("limit_req ")]
    assert proxy_directives == [
        "proxy_pass http://127.0.0.1:8010",
        "include /etc/nginx/proxy_params",
        "proxy_set_header X-Forwarded-Proto https",
        "proxy_read_timeout 120s",
    ]
    assert proxy_directives == [d for d in locations["/"] if not d.startswith("limit_req ")]


def test_rate_zones_keep_existing_per_client_rates(nginx_config):
    source, _ = nginx_config
    zones = {" ".join(d.split()) for d in re.findall(r"\blimit_req_zone\s+[^;]+;", source)}
    assert zones == {
        "limit_req_zone $binary_remote_addr zone=sim_login:10m rate=10r/m;",
        "limit_req_zone $binary_remote_addr zone=sim_api:10m rate=600r/m;",
    }


def test_evolution_webhook_is_still_blocked_at_public_proxy(nginx_config):
    _, locations = nginx_config
    assert locations["/hooks/evolution/"] == ["return 404"]
