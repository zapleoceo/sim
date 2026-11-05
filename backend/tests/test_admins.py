from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import AuthSession
from tests.conftest import OPERATOR, OWNER, SUPERADMIN, login, widget_payload


async def sa_headers(api) -> dict[str, str]:
    return {"X-CSRF-Token": await login(api, *SUPERADMIN)}


async def admin_id(api, tg_id: int) -> int:
    items = (await api.get("/api/admins")).json()["items"]
    return next(a["id"] for a in items if a["telegram_id"] == tg_id)


async def test_requires_superadmin_and_csrf(api):
    assert (await api.get("/api/admins")).status_code == 401
    csrf = await login(api, *SUPERADMIN)
    assert (await api.get("/api/admins")).status_code == 200
    assert (await api.post("/api/admins", json={"telegram_id": 5, "role": "operator"})).status_code == 403
    assert csrf


async def test_list_shape(api):
    await sa_headers(api)
    items = (await api.get("/api/admins")).json()["items"]
    assert {a["telegram_id"] for a in items} == {OWNER[0], OPERATOR[0], SUPERADMIN[0]}
    assert {"id", "role", "telegram_id", "tg_username", "display_name", "photo_url", "is_active"} <= set(items[0])


async def test_create_by_id_and_by_username(api):
    h = await sa_headers(api)
    by_id = await api.post("/api/admins", json={"telegram_id": 555, "role": "operator"}, headers=h)
    assert by_id.status_code == 201 and by_id.json()["telegram_id"] == 555
    assert by_id.json()["is_active"] is True
    by_name = await api.post(
        "/api/admins", json={"tg_username": "@Some_One", "role": "owner", "display_name": "Some"}, headers=h
    )
    assert by_name.status_code == 201
    assert by_name.json()["tg_username"] == "some_one" and by_name.json()["telegram_id"] is None
    assert by_name.json()["display_name"] == "Some"


async def test_create_validation_and_duplicates(api):
    h = await sa_headers(api)
    assert (await api.post("/api/admins", json={"role": "operator"}, headers=h)).status_code == 422
    bad = await api.post("/api/admins", json={"tg_username": "a b!", "role": "operator"}, headers=h)
    assert bad.status_code == 422
    assert (await api.post("/api/admins", json={"telegram_id": OWNER[0]}, headers=h)).status_code == 409
    await api.post("/api/admins", json={"tg_username": "dup_name"}, headers=h)
    assert (await api.post("/api/admins", json={"tg_username": "@DUP_name"}, headers=h)).status_code == 409


async def test_patch_role_and_deactivate_revokes_sessions(api, container):
    h = await sa_headers(api)
    op_id = await admin_id(api, OPERATOR[0])
    await api.post("/api/auth/logout", headers=h)
    await login(api, *OPERATOR)  # the operator has a live session
    api.cookies.clear()  # keep the operator's server-side session alive
    h = await sa_headers(api)
    async with container.factory() as s:
        assert (await s.scalars(select(AuthSession).where(AuthSession.user_id == op_id))).all()
    changed = await api.patch(f"/api/admins/{op_id}", json={"role": "owner"}, headers=h)
    assert changed.status_code == 200 and changed.json()["role"] == "owner"
    off = await api.patch(f"/api/admins/{op_id}", json={"is_active": False}, headers=h)
    assert off.status_code == 200 and off.json()["is_active"] is False
    async with container.factory() as s:
        assert not (await s.scalars(select(AuthSession).where(AuthSession.user_id == op_id))).all()
    await api.post("/api/auth/logout", headers=h)
    resp = await api.post("/api/auth/telegram", json=widget_payload(OPERATOR[0]))
    assert resp.status_code == 403


async def test_delete_admin(api):
    h = await sa_headers(api)
    op_id = await admin_id(api, OPERATOR[0])
    assert (await api.delete(f"/api/admins/{op_id}", headers=h)).status_code == 204
    assert op_id not in [a["id"] for a in (await api.get("/api/admins")).json()["items"]]
    assert (await api.delete(f"/api/admins/{op_id}", headers=h)).status_code == 404


async def test_superadmin_cannot_touch_self(api):
    h = await sa_headers(api)
    me = await admin_id(api, SUPERADMIN[0])
    # a second superadmin exists, so only the self-rule can be what blocks this
    await api.post("/api/admins", json={"telegram_id": 556, "role": "superadmin"}, headers=h)
    assert (await api.patch(f"/api/admins/{me}", json={"role": "owner"}, headers=h)).status_code == 409
    assert (await api.patch(f"/api/admins/{me}", json={"is_active": False}, headers=h)).status_code == 409
    assert (await api.delete(f"/api/admins/{me}", headers=h)).status_code == 409
    same = await api.patch(f"/api/admins/{me}", json={"role": "superadmin"}, headers=h)
    assert same.status_code == 200  # no-op is fine


async def test_last_active_superadmin_cannot_be_demoted_deactivated_or_deleted(api, container):
    """Through the API the self-rule fires first; the service rule is exercised directly."""
    from sim.adapters.db.session import transaction
    from sim.domain.enums import Role
    from sim.services.admins import AdminError

    h = await sa_headers(api)
    me = await admin_id(api, SUPERADMIN[0])
    other = (await api.post("/api/admins", json={"telegram_id": 556, "role": "superadmin"}, headers=h)).json()["id"]
    async with transaction(container.factory) as s:
        svc = container.admins(s)
        actor = await svc.get_or_404(other)
        await svc.update(actor, me, role=None, is_active=False)  # fine: two were active
    async with transaction(container.factory) as s:
        svc = container.admins(s)
        actor = await svc.get_or_404(me)  # deactivated row used as an actor object
        for call in (
            lambda: svc.update(actor, other, role=None, is_active=False),
            lambda: svc.update(actor, other, role=Role.OWNER, is_active=None),
            lambda: svc.delete(actor, other),
        ):
            try:
                await call()
            except AdminError as exc:
                assert exc.status == 409
            else:
                raise AssertionError("last superadmin was removable")
