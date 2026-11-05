from __future__ import annotations

from datetime import timedelta

import pytest

from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Role
from sim.services.auth import (
    AuthError,
    AuthService,
    LoginRateLimiter,
    TooManyAttempts,
    hash_token,
)
from tests.conftest import OWNER, login


async def make_service(container, **kw):
    async with transaction(container.factory) as s:
        await AuthService(s, ttl_h=1, limiter=LoginRateLimiter()).create_user(
            "ann", "pw-12345678", Role.OPERATOR
        )
    return container


async def test_login_stores_only_token_hash(container):
    await make_service(container)
    async with transaction(container.factory) as s:
        res = await AuthService(s, ttl_h=1, limiter=LoginRateLimiter()).login("ann", "pw-12345678")
    async with container.factory() as s:
        row = await AuthService(s, ttl_h=1, limiter=LoginRateLimiter()).sessions.by_token_hash(
            hash_token(res.token)
        )
        assert row is not None and row.token_hash != res.token and row.csrf_token == res.csrf_token


@pytest.mark.parametrize(("user", "pw"), [("ann", "wrong"), ("ghost", "pw-12345678")])
async def test_wrong_credentials_give_identical_error(container, user, pw):
    await make_service(container)
    async with container.factory() as s:
        svc = AuthService(s, ttl_h=1, limiter=LoginRateLimiter())
        with pytest.raises(AuthError) as exc:
            await svc.login(user, pw)
    assert str(exc.value) == "Invalid username or password"


async def test_rate_limit_blocks_after_repeated_failures(container):
    await make_service(container)
    limiter = LoginRateLimiter(max_failures=3)
    async with container.factory() as s:
        svc = AuthService(s, ttl_h=1, limiter=limiter)
        for _ in range(3):
            with pytest.raises(AuthError):
                await svc.login("ann", "bad", "1.1.1.1")
        with pytest.raises(TooManyAttempts):
            await svc.login("ann", "pw-12345678", "1.1.1.1")  # even the right password


async def test_expired_session_is_rejected(container):
    await make_service(container)
    async with transaction(container.factory) as s:
        res = await AuthService(s, ttl_h=1, limiter=LoginRateLimiter()).login("ann", "pw-12345678")
    later = utcnow() + timedelta(hours=2)
    async with container.factory() as s:
        svc = AuthService(s, ttl_h=1, limiter=LoginRateLimiter(), clock=lambda: later)
        assert await svc.authenticate(res.token) is None


async def test_bootstrap_owner_is_idempotent(container):
    async with transaction(container.factory) as s:
        svc = AuthService(s, ttl_h=1, limiter=LoginRateLimiter())
        assert await svc.bootstrap_owner("o", "pw") is not None
        assert await svc.bootstrap_owner("o2", "pw") is None


# ---- through the API ----------------------------------------------------------------------
async def test_api_login_me_logout(api):
    assert (await api.get("/api/auth/me")).status_code == 401
    csrf = await login(api, *OWNER)
    me = await api.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["csrf_token"] == csrf
    assert me.json()["user"]["role"] == "owner"
    assert (await api.post("/api/auth/logout")).status_code == 204
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_api_wrong_password_is_401(api):
    resp = await api.post("/api/auth/login", json={"username": OWNER[0], "password": "nope"})
    assert resp.status_code == 401 and resp.json()["detail"] == "Invalid username or password"


async def test_cookie_flags(api):
    resp = await api.post("/api/auth/login", json={"username": OWNER[0], "password": OWNER[1]})
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


async def test_csrf_required_on_mutations(api):
    csrf = await login(api, *OWNER)
    body = {"name": "P1"}
    assert (await api.post("/api/personas", json=body)).status_code == 403
    assert (
        await api.post("/api/personas", json=body, headers={"X-CSRF-Token": "wrong"})
    ).status_code == 403
    ok = await api.post("/api/personas", json=body, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201


async def test_operator_cannot_edit_personas_or_channels(api):
    owner_csrf = await login(api, *OWNER)
    created = await api.post("/api/personas", json={"name": "P1"}, headers={"X-CSRF-Token": owner_csrf})
    pid = created.json()["id"]
    await api.post("/api/auth/logout")

    csrf = await login(api, "op", "operator-pass-1")
    h = {"X-CSRF-Token": csrf}
    assert (await api.get("/api/personas")).status_code == 200
    assert (await api.post("/api/personas", json={"name": "X"}, headers=h)).status_code == 403
    assert (await api.put(f"/api/personas/{pid}", json={"name": "Y"}, headers=h)).status_code == 403
    assert (await api.delete(f"/api/personas/{pid}", headers=h)).status_code == 403
    assert (await api.post("/api/strategies", json={"name": "S"}, headers=h)).status_code == 403
    assert (
        await api.post("/api/channels", json={"kind": "telegram", "persona_id": pid}, headers=h)
    ).status_code == 403
    assert (await api.get("/api/models")).status_code == 403
