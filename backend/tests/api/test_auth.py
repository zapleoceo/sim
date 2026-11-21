"""Dashboard login: widget signature, admins only, server-side session, CSRF, rate limit."""
from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.auth import (
    SESSION_COOKIE,
    AuthError,
    NotConfigured,
    hash_token,
    verify_telegram_widget,
)
from sim.api.ratelimit import LoginRateLimiter, TooManyAttempts
from sim.clock import FixedClock
from sim.db.models import AdminUser, AuthSession
from tests.api.fakes import FakeDeps
from tests.api.helpers import (
    ADMIN_TG_ID,
    BOT_TOKEN,
    ApiClient,
    World,
    login,
    make_admin,
    signed_widget,
)

NOW = 1_800_000_000


# --- widget signature (no database) ----------------------------------------------------------


def test_widget_valid_signature() -> None:
    profile = verify_telegram_widget(signed_widget(42, auth_date=NOW, last_name="Петров"),
                                     BOT_TOKEN, now=NOW + 10)
    assert profile.id == 42
    assert profile.username == "dima"
    assert profile.name == "Дима Петров"


def test_widget_tampered_field_is_rejected() -> None:
    data = {**signed_widget(42, auth_date=NOW), "id": 43}
    with pytest.raises(AuthError):
        verify_telegram_widget(data, BOT_TOKEN, now=NOW)


def test_widget_signed_by_another_bot_is_rejected() -> None:
    with pytest.raises(AuthError):
        verify_telegram_widget(signed_widget(42, auth_date=NOW, token="999:other"), BOT_TOKEN,
                               now=NOW)


def test_widget_too_old_or_from_the_future_is_rejected() -> None:
    with pytest.raises(AuthError):
        verify_telegram_widget(signed_widget(42, auth_date=NOW - 3601), BOT_TOKEN, now=NOW)
    with pytest.raises(AuthError):
        verify_telegram_widget(signed_widget(42, auth_date=NOW + 600), BOT_TOKEN, now=NOW)


def test_widget_without_bot_token_is_not_configured() -> None:
    with pytest.raises(NotConfigured):
        verify_telegram_widget(signed_widget(42, auth_date=NOW), "", now=NOW)


def test_rate_limiter_blocks_pair_after_max_failures_and_resets() -> None:
    limiter = LoginRateLimiter(max_failures=2, window_s=60, ip_max_failures=10)
    keys = limiter.keys_for("42", "1.2.3.4")
    limiter.record_failure(*keys, now=0)
    limiter.check(*keys, now=1)
    limiter.record_failure(*keys, now=2)
    with pytest.raises(TooManyAttempts):
        limiter.check(*keys, now=3)
    limiter.check(*limiter.keys_for("43", "1.2.3.4"), now=3)   # another id from the same ip
    limiter.check(*keys, now=100)                                # the window has passed
    limiter.record_failure(*keys, now=101)
    limiter.reset(*keys)
    limiter.check(*keys, now=102)


# --- routes ----------------------------------------------------------------------------------


async def test_config_is_public(client: ApiClient) -> None:
    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {"bot_username": "sim_login_bot"}


async def test_login_sets_session_cookie_and_me_works(client: ApiClient, db: AsyncSession,
                                                     clock: FixedClock) -> None:
    admin_id = await make_admin(db)
    body = await login(client, clock)
    assert body["admin"] == {"id": admin_id, "name": "Дима", "username": "dima"}
    assert client.cookies.get(SESSION_COOKIE)
    stored = (await db.scalars(select(AuthSession).where(AuthSession.admin_id == admin_id))).one()
    assert stored.token_hash == hash_token(client.cookies[SESSION_COOKIE])
    assert stored.expires_at == clock.now() + timedelta(hours=72)

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"admin": body["admin"], "csrf": body["csrf"]}


async def test_login_cookie_flags(client: ApiClient, db: AsyncSession,
                                  clock: FixedClock) -> None:
    await make_admin(db)
    response = await client.post("/api/auth/telegram", json=signed_widget(
        ADMIN_TG_ID, auth_date=int(clock.now().timestamp())))
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie


async def test_login_with_bad_signature_is_401(client: ApiClient, db: AsyncSession,
                                               clock: FixedClock) -> None:
    await make_admin(db)
    data = {**signed_widget(ADMIN_TG_ID, auth_date=int(clock.now().timestamp())),
            "hash": "0" * 64}
    response = await client.post("/api/auth/telegram", json=data)
    assert response.status_code == 401
    assert "error" in response.json()


async def test_login_with_expired_widget_is_401(client: ApiClient, db: AsyncSession,
                                                clock: FixedClock) -> None:
    await make_admin(db)
    data = signed_widget(ADMIN_TG_ID, auth_date=int(clock.now().timestamp()) - 7200)
    response = await client.post("/api/auth/telegram", json=data)
    assert response.status_code == 401


async def test_login_of_unknown_or_inactive_account_is_403(client: ApiClient, db: AsyncSession,
                                                           clock: FixedClock) -> None:
    await make_admin(db, tg_id=2002, active=False)
    for tg_id in (2001, 2002):
        data = signed_widget(tg_id, auth_date=int(clock.now().timestamp()))
        response = await client.post("/api/auth/telegram", json=data)
        assert response.status_code == 403
        assert response.json() == {"error": "Доступ не выдан"}


async def test_superadmin_from_settings_is_created_on_first_login(
        client: ApiClient, db: AsyncSession, clock: FixedClock, deps: FakeDeps) -> None:
    deps.settings.superadmin_telegram_ids = "3003"
    body = await login(client, clock, tg_id=3003)
    admin = await db.scalar(select(AdminUser).where(AdminUser.telegram_id == 3003))
    assert admin is not None and admin.is_active
    assert body["admin"]["id"] == admin.id


async def test_too_many_failed_logins_is_429(client: ApiClient, clock: FixedClock) -> None:
    bad = {**signed_widget(5005, auth_date=int(clock.now().timestamp())), "hash": "f" * 64}
    for _ in range(5):
        assert (await client.post("/api/auth/telegram", json=bad)).status_code == 401
    response = await client.post("/api/auth/telegram", json=bad)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


async def test_api_requires_login(client: ApiClient, world: World) -> None:
    for path in ("/api/auth/me", "/api/chats", f"/api/chats/{world.chat_id}", "/api/persona"):
        response = await client.get(path)
        assert response.status_code == 401, path
        assert "error" in response.json()


async def test_expired_session_is_401(client: ApiClient, db: AsyncSession,
                                      clock: FixedClock) -> None:
    admin_id = await make_admin(db)
    cookie_value = secrets.token_urlsafe(32)
    db.add(AuthSession(admin_id=admin_id, token_hash=hash_token(cookie_value), csrf_token="c",
                       expires_at=clock.now() - timedelta(minutes=1)))
    await db.commit()
    response = await client.get("/api/auth/me",
                                headers={"Cookie": f"{SESSION_COOKIE}={cookie_value}"})
    assert response.status_code == 401


async def test_deactivated_admin_loses_the_session(api: ApiClient, db: AsyncSession) -> None:
    admin = await db.scalar(select(AdminUser).where(AdminUser.telegram_id == ADMIN_TG_ID))
    assert admin is not None
    admin.is_active = False
    await db.commit()
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_mutation_without_or_with_wrong_csrf_is_403(api: ApiClient, world: World) -> None:
    csrf = api.headers.pop("X-CSRF-Token")
    path = f"/api/chats/{world.chat_id}/read"
    missing = await api.post(path)
    assert missing.status_code == 403
    assert "error" in missing.json()
    wrong = await api.post(path, headers={"X-CSRF-Token": "nope"})
    assert wrong.status_code == 403
    ok = await api.post(path, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 204


async def test_logout_deletes_the_session(api: ApiClient, db: AsyncSession) -> None:
    response = await api.post("/api/auth/logout")
    assert response.status_code == 204
    assert (await db.scalars(select(AuthSession))).all() == []
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_logout_of_live_session_needs_csrf(api: ApiClient, db: AsyncSession) -> None:
    api.headers.pop("X-CSRF-Token")
    assert (await api.post("/api/auth/logout")).status_code == 403
    assert len((await db.scalars(select(AuthSession))).all()) == 1


async def test_logout_without_session_just_clears_cookie(client: ApiClient) -> None:
    assert (await client.post("/api/auth/logout")).status_code == 204
