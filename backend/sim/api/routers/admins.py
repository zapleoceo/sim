from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from sim.api.deps import App, Db, Superadmin
from sim.api.schemas import AdminCreateIn, AdminPatchIn, admin_out
from sim.services.admins import AdminError

router = APIRouter(prefix="/api/admins", tags=["admins"])


def http_error(exc: AdminError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
async def list_admins(p: Superadmin, c: App, s: Db):
    return {"items": [admin_out(u) for u in await c.admins(s).list_all()]}


@router.post("", status_code=201)
async def create_admin(body: AdminCreateIn, p: Superadmin, c: App, s: Db):
    try:
        user = await c.admins(s).create(**body.model_dump())
    except AdminError as exc:
        raise http_error(exc) from exc
    return admin_out(user)


@router.patch("/{admin_id}")
async def patch_admin(admin_id: int, body: AdminPatchIn, p: Superadmin, c: App, s: Db):
    try:
        user = await c.admins(s).update(p.user, admin_id, role=body.role, is_active=body.is_active)
    except AdminError as exc:
        raise http_error(exc) from exc
    return admin_out(user)


@router.delete("/{admin_id}", status_code=204)
async def delete_admin(admin_id: int, p: Superadmin, c: App, s: Db) -> Response:
    try:
        await c.admins(s).delete(p.user, admin_id)
    except AdminError as exc:
        raise http_error(exc) from exc
    return Response(status_code=204)
