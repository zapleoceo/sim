"""Owner settings: prompt texts and tunables stored in the DB (see sim.services.app_settings)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from sim.api.deps import App, Authed, Db, Owner
from sim.api.schemas import Body, dt
from sim.services.app_settings import AppSettingsService, SettingError, SettingItem, env_defaults
from sim.services.guard import GUARD_CONTROL

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingIn(Body):
    value: Any


def setting_out(item: SettingItem) -> dict[str, Any]:
    spec = item.spec
    return {
        "key": spec.key, "group": spec.group, "label": spec.label, "help": spec.help,
        "kind": spec.kind, "min": spec.min, "max": spec.max,
        "value": item.value, "default": item.default, "is_default": item.is_default,
        "updated_at": dt(item.updated_at), "updated_by_id": item.updated_by_id,
    }


def service(c: App, s: Db) -> AppSettingsService:
    return AppSettingsService(s, env_defaults(c.settings))


def not_found_or_invalid(exc: SettingError) -> HTTPException:
    return HTTPException(404 if str(exc).startswith("unknown setting") else 422, str(exc))


@router.get("")
async def list_settings(c: App, s: Db, _: Authed):
    """`fixed`: the fixed decision-control contract (persona is empty), shown read-only so the owner sees every prompt."""
    return {
        "items": [setting_out(i) for i in await service(c, s).items()],
        "fixed": {"persona": "", "guard": GUARD_CONTROL},
    }


@router.put("/{key}")
async def put_setting(key: str, body: SettingIn, c: App, s: Db, p: Owner):
    try:
        return setting_out(await service(c, s).set(key, body.value, p.user.id))
    except SettingError as exc:
        raise not_found_or_invalid(exc) from exc


@router.delete("/{key}")
async def reset_setting(key: str, c: App, s: Db, _: Owner):
    """Back to the code default."""
    try:
        return setting_out(await service(c, s).reset(key))
    except SettingError as exc:
        raise not_found_or_invalid(exc) from exc
