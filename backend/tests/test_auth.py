from __future__ import annotations

import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import AdminUser, AuthSession
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Role
from sim.services.auth import (
    AccessDenied,
    AuthError,
    AuthService,
    LoginRateLimiter,
    hash_token,
    verify_telegram_widget,
)
from tests.conftest import (
    BOT_TOKEN,
    BOT_USERNAME,
    OPERATOR,
    OWNER,
    SUPERADMIN,
    login,
    sign_widget,
    widget_payload,
)

TG = "/api/auth/telegram"


def service(container, s, **kw) -> AuthService:
    return AuthService(s, ttl_h=1, limiter=LoginRateLimiter(), bot_token=BOT_TOKEN, **kw)


# ---- widget verification (pure) ------------------------------------------------------------
def test_widget_valid_signature_is_accepted():
    data = widget_payload(5, username="Ann", last_name="Lee")
    profile = verify_telegram_widget(data, BOT_TOKEN)
    assert profile.id == 5 and profile.username == "ann" and profile.display_name == "Test Lee"


def test_widget_bad_hash_rejected():
    data = {**widget_payload(5), "hash": "0" * 64}
    with pytest.raises(AuthError):
        verify_telegram_widget(data, BOT_TOKEN)


def test_widget_tampered_field_rejected():
    data = {**widget_payload(5), "id": 6}
    with pytest.raises(AuthError):
        verify_telegram_widget(data, BOT_TOKEN)


def test_widget_signed_with_other_token_rejected():
    data = sign_widget({"id": 5, "auth_date": int(time.time())}, token="1:other")
    with pytest.raises(AuthError):
        verify_telegram_widget(data, BOT_TOKEN)


def test_widget_stale_rejected_and_boundary_ok():
    now = time.time()
    stale = sign_widget({"id": 5, "auth_date": int(now) - 3601})
    with pytest.raises(AuthError):
        verify_telegram_widget(stale, BOT_TOKEN, now=now)
    fresh = sign_widget({"id": 5, "auth_date": int(now) - 3500})
    assert verify_telegram_widget(fresh, BOT_TOKEN, now=now).id == 5


def test_widget_future_rejected_small_skew_ok():
    now = time.time()
    future = sign_widget({"id": 5, "auth_date": int(now) + 120})
    with pytest.raises(AuthError):
        verify_telegram_widget(future, BOT_TOKEN, now=now)
    skew = sign_widget({"id": 5, "auth_date": int(now) + 30})
    assert verify_telegram_widget(skew, BOT_TOKEN, now=now).id == 5


# ---- through the API -----------------------------------------------------------------------
async def test_config_is_public(api):
    resp = await api.get("/api/auth/config")
    assert resp.status_code == 200 and resp.json() == {"bot_username": BOT_USERNAME}


async def test_api_login_me_logout(api):
    assert (await api.get("/api/auth/me")).status_code == 401
    csrf = await login(api, *OWNER)
    me = await api.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["csrf_token"] == csrf
    user = me.json()["user"]
    assert user["role"] == "owner" and user["telegram_id"] == OWNER[0]
    assert set(user) == {"id", "role", "telegram_id", "tg_username", "display_name", "photo_url"}
    assert (await api.post("/api/auth/logout")).status_code == 403          # no CSRF token
    assert (await api.post("/api/auth/logout", headers={"X-CSRF-Token": "bad"})).status_code == 403
    assert (await api.get("/api/auth/me")).status_code == 200               # still logged in
    assert (await api.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 204
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_logout_without_live_session_clears_cookie(api):
    resp = await api.post("/api/auth/logout")
    assert resp.status_code == 204
    api.cookies.set("sim_session", "stale-token")
    resp = await api.post("/api/auth/logout")
    assert resp.status_code == 204 and "sim_session" in resp.headers["set-cookie"]


async def test_login_updates_profile(api, container):
    payload = widget_payload(
        OWNER[0], first_name="Dima", last_name="Z", username="DimaZ", photo_url="https://t.me/i/u.jpg"
    )
    assert (await api.post(TG, json=payload)).status_code == 200
    user = (await api.get("/api/auth/me")).json()["user"]
    assert user["display_name"] == "Dima Z" and user["tg_username"] == "dimaz"
    assert user["photo_url"] == "https://t.me/i/u.jpg"


async def test_replayed_hash_is_rejected(api):
    payload = widget_payload(OWNER[0])
    assert (await api.post(TG, json=payload)).status_code == 200
    again = await api.post(TG, json=payload)
    assert again.status_code == 401


async def test_bad_signature_and_stale_are_401(api):
    assert (await api.post(TG, json={**widget_payload(OWNER[0]), "hash": "1" * 64})).status_code == 401
    old = sign_widget({"id": OWNER[0], "auth_date": int(time.time()) - 7200})
    assert (await api.post(TG, json=old)).status_code == 401
    future = sign_widget({"id": OWNER[0], "auth_date": int(time.time()) + 600})
    assert (await api.post(TG, json=future)).status_code == 401


async def test_unknown_telegram_id_is_403_with_id(api):
    resp = await api.post(TG, json=widget_payload(999_000_111))
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Доступ не выдан", "telegram_id": 999_000_111}
    assert "set-cookie" not in resp.headers


async def test_unknown_id_still_consumes_hash(api):
    payload = widget_payload(999_000_111)
    assert (await api.post(TG, json=payload)).status_code == 403
    assert (await api.post(TG, json=payload)).status_code == 401  # replay, not 403 again


async def test_inactive_admin_is_403(api, container):
    async with transaction(container.factory) as s:
        row = await container.auth(s).users.by_telegram_id(OPERATOR[0])
        row.is_active = False
    resp = await api.post(TG, json=widget_payload(OPERATOR[0]))
    assert resp.status_code == 403 and resp.json()["telegram_id"] == OPERATOR[0]


async def test_invite_by_username_binds_telegram_id(api, container):
    async with transaction(container.factory) as s:
        s.add(AdminUser(tg_username="newbie", role=Role.OPERATOR, display_name="@newbie"))
    resp = await api.post(TG, json=widget_payload(777, username="NewBie"))
    assert resp.status_code == 200
    assert resp.json()["user"]["telegram_id"] == 777
    async with container.factory() as s:
        row = await container.auth(s).users.by_tg_username("newbie")
        assert row.telegram_id == 777
    # username of a bound admin cannot be taken over by a different id
    other = await api.post(TG, json=widget_payload(778, username="newbie"))
    assert other.status_code == 403


async def test_cookie_flags(api):
    resp = await api.post(TG, json=widget_payload(OWNER[0]))
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


async def test_password_login_route_is_gone(api):
    resp = await api.post("/api/auth/login", json={"username": "a", "password": "b"})
    assert resp.status_code in (404, 405)


async def test_login_not_configured_is_503(api, container):
    container.settings.tg_login_bot_token = ""
    resp = await api.post(TG, json=widget_payload(OWNER[0]))
    assert resp.status_code == 503
    assert (await api.get("/api/auth/config")).json() == {"bot_username": BOT_USERNAME}


async def test_expired_session_is_rejected(api, container):
    async with transaction(container.factory) as s:
        res = await service(container, s).login_telegram(widget_payload(OPERATOR[0]))
    async with container.factory() as s:
        row = (await s.scalars(select(AuthSession))).all()[-1]
        assert row.token_hash == hash_token(res.token) != res.token
    later = utcnow() + timedelta(hours=2)
    async with container.factory() as s:
        assert await service(container, s, clock=lambda: later).authenticate(res.token) is None


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
    await api.post("/api/auth/logout", headers={"X-CSRF-Token": owner_csrf})

    csrf = await login(api, *OPERATOR)
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
    assert (await api.get("/api/admins")).status_code == 403


async def test_superadmin_passes_owner_only_routes(api):
    csrf = await login(api, *SUPERADMIN)
    h = {"X-CSRF-Token": csrf}
    assert (await api.post("/api/personas", json={"name": "S1"}, headers=h)).status_code == 201
    assert (await api.post("/api/strategies", json={"name": "S"}, headers=h)).status_code == 201
    assert (await api.get("/api/models")).status_code != 403


async def test_owner_is_not_superadmin(api):
    await login(api, *OWNER)
    assert (await api.get("/api/admins")).status_code == 403


def test_has_owner_rights_helper():
    assert Role.SUPERADMIN.has_owner_rights and Role.OWNER.has_owner_rights
    assert not Role.OPERATOR.has_owner_rights


async def test_sync_superadmins_is_idempotent_and_reactivates(api, container):
    async with transaction(container.factory) as s:
        assert await container.auth(s).sync_superadmins([42, OWNER[0]]) == 2
    async with transaction(container.factory) as s:
        assert await container.auth(s).sync_superadmins([42, OWNER[0]]) == 0
        row = await container.auth(s).users.by_telegram_id(42)
        row.is_active = False
    async with transaction(container.factory) as s:
        assert await container.auth(s).sync_superadmins([42]) == 1
        owner = await container.auth(s).users.by_telegram_id(OWNER[0])
        assert owner.role is Role.SUPERADMIN


# ---- break-glass login link ----------------------------------------------------------------
async def test_login_link_is_one_time(api, container):
    async with transaction(container.factory) as s:
        token = await container.auth(s).create_login_link(SUPERADMIN[0])
    async with container.factory() as s:  # only the hash is stored
        from sim.adapters.db.models import LoginLink

        row = (await s.scalars(select(LoginLink))).one()
        assert row.token_hash == hash_token(token) and token not in row.token_hash
    first = await api.get("/api/auth/link", params={"token": token})
    assert first.status_code == 303 and first.headers["location"] == "/"
    assert "sim_session" in first.headers["set-cookie"]
    assert (await api.get("/api/auth/me")).json()["user"]["telegram_id"] == SUPERADMIN[0]
    second = await api.get("/api/auth/link", params={"token": token})
    assert second.status_code == 401


async def test_login_link_expires(api, container):
    async with transaction(container.factory) as s:
        token = await container.auth(s).create_login_link(OWNER[0])
    later = utcnow() + timedelta(minutes=11)
    async with transaction(container.factory) as s:
        with pytest.raises(AuthError):
            await service(container, s, clock=lambda: later).login_with_link(token)


async def test_login_link_only_for_active_admin(api, container):
    async with transaction(container.factory) as s:
        with pytest.raises(AccessDenied):
            await container.auth(s).create_login_link(424242)
    async with transaction(container.factory) as s:
        token = await container.auth(s).create_login_link(OPERATOR[0])
        (await container.auth(s).users.by_telegram_id(OPERATOR[0])).is_active = False
    async with transaction(container.factory) as s:
        with pytest.raises(AuthError):
            await container.auth(s).login_with_link(token)
    async with transaction(container.factory) as s:
        with pytest.raises(AccessDenied):
            await container.auth(s).create_login_link(OPERATOR[0])


async def test_login_link_garbage_token(api):
    assert (await api.get("/api/auth/link", params={"token": "nope"})).status_code == 401
