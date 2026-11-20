"""Private owner media studio. No paid fallback or direct provider credentials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from sim.adapters.db.models import Job
from sim.adapters.llm.broker import BrokerLLM
from sim.adapters.studio_broker import StudioBroker
from sim.api.deps import App, Db, Owner
from sim.ports.llm import LLMError
from sim.ports.speech import SpeechError
from sim.ports.video import VideoError
from sim.services import preview_operations, studio_chat
from sim.services.app_settings import AppSettingsService, env_defaults
from sim.services.media_routes import route_plan
from sim.services.studio_drafts import DraftConflict, StudioDrafts
from sim.services.studio_media import (
    MediaRequest,
    StudioConflict,
    StudioMedia,
    StudioUnavailable,
)

router = APIRouter(prefix="/api/studio", tags=["studio"])


def personal_owner(c: App, p: Owner) -> bool:
    return bool(c.settings.studio_owner_telegram_id) and (
        p.user.telegram_id == c.settings.studio_owner_telegram_id
    )


def draft_scope(c: App, p: Owner) -> str:
    # Studio has one configured reference character, not a selected CRM persona.
    # Rebinding its owner/reference must not silently reuse the previous character's draft.
    return hashlib.sha256(json.dumps([
        p.user.id, c.settings.studio_owner_telegram_id,
        c.settings.studio_photo_filename, c.settings.studio_video_filename,
    ]).encode()).hexdigest()


class DraftIn(BaseModel):
    scope: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=0)
    text: str = Field(max_length=2000)


@router.get("/media/draft/video")
async def read_video_draft(c: App, p: Owner, response: Response):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return {"scope": draft_scope(c, p), **await StudioDrafts(c.factory, c.box).read(
            p.user.id, draft_scope(c, p))}
    except (ValueError, SQLAlchemyError) as exc:
        raise HTTPException(503, "Draft could not be read") from exc


@router.put("/media/draft/video")
async def save_video_draft(body: DraftIn, c: App, p: Owner, response: Response):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    if body.scope != draft_scope(c, p):
        raise HTTPException(409, "Studio reference changed; reload the draft")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return {"scope": body.scope, **await StudioDrafts(c.factory, c.box).save(
            p.user.id, body.scope, body.revision, body.text)}
    except DraftConflict as exc:
        raise HTTPException(409, "Draft changed in another window; keep your text") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Draft was not confirmed saved") from exc


def asset(c: App, kind: str) -> Path | None:
    name = (c.settings.studio_photo_filename if kind == "photo"
            else c.settings.studio_video_filename)
    root = (Path(c.settings.media_dir) / "studio").resolve()
    path = (root / name).resolve()
    allowed = {".jpg", ".jpeg", ".png", ".webp"} if kind == "photo" else {".mp4", ".webm"}
    if not name or not path.is_relative_to(root) or path.suffix.lower() not in allowed:
        return None
    return path if path.is_file() else None


@router.get("")
async def status(c: App, p: Owner, s: Db):
    personal = personal_owner(c, p)
    models: list[str] = []
    detail = "Генерация речи и видео в Studio выключена. Возможности и права Broker для неё не проверены."
    if not personal:
        detail += " Personal subscription access is not configured for this account."
    else:
        try:
            models = await studio_chat.subscription_models(c)
        except LLMError:
            detail += " Broker model catalog is unavailable."
        if not models:
            detail += " No verified available Sonnet/Sol subscription route."
    return {
        "photo_url": "/api/studio/assets/photo" if personal and asset(c, "photo") else None,
        "video_url": "/api/studio/assets/video" if personal and asset(c, "video") else None,
        "models": models, "text_available": bool(models), "media_available": False,
        "speech_available": False, "video_available": False,
        "media_status": "disabled", "capabilities_verified": False,
        "personal_access": personal,
        "draft_scope": draft_scope(c, p) if personal else None,
        "detail": detail,
        "route_plan": route_plan(await AppSettingsService(s, env_defaults(c.settings)).load())
        if personal else [],
    }


def media_service(c: App) -> StudioMedia:
    # Deliberately no setting/query switch. A future reviewed activation must verify
    # identity, provider grants, budgets, bounded responses and recovery contract first.
    bounded = StudioBroker(c.llm) if isinstance(c.llm, BrokerLLM) else None
    return StudioMedia(c.factory, c.box, bounded, None, remote=bounded)


def operation_out(row):
    return {"operation_id": row.id, "kind": row.kind, "status": row.status,
            "created_at": row.created_at.isoformat() + "Z",
            "artifact_url": f"/api/studio/media/{row.id}/artifact" if row.status == "complete" else None}


@router.get("/media")
async def media_history(c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    return {"operations": [operation_out(row) for row in await media_service(c).recent(p.user.id)]}


@router.post("/media/{operation_id}/reconcile")
async def reconcile_media(operation_id: UUID, c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    try:
        row = await media_service(c).reconcile(p.user.id, str(operation_id))
    except StudioUnavailable as exc:
        raise HTTPException(423, "Сверка Studio выключена; внешний запрос не отправлен") from exc
    except (StudioConflict, VideoError) as exc:
        raise HTTPException(409, "Результат не подтверждён; повторная генерация запрещена") from exc
    return operation_out(row)


@router.post("/media")
async def generate_media(body: MediaRequest, c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    try:
        row = await media_service(c).execute(p.user.id, body)
    except StudioUnavailable as exc:
        raise HTTPException(423, "Генерация Studio выключена; запрос провайдеру не отправлен") from exc
    except (StudioConflict, SpeechError, VideoError) as exc:
        raise HTTPException(409, "Результат не подтверждён. Не повторяйте генерацию; требуется проверка операции") from exc
    return operation_out(row)


@router.get("/media/{operation_id}")
async def media_status(operation_id: UUID, c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    row = await media_service(c).get(p.user.id, str(operation_id), include_artifact=False)
    if row is None:
        raise HTTPException(404, "Operation not found")
    return operation_out(row)


@router.get("/media/{operation_id}/artifact")
async def media_artifact(operation_id: UUID, c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    service = media_service(c)
    row = await service.get(p.user.id, str(operation_id))
    if row is None or row.status != "complete":
        raise HTTPException(404, "Confirmed artifact not found")
    try:
        data, mime = service.artifact(row)
    except (StudioConflict, ValueError, KeyError) as exc:
        raise HTTPException(409, "Сохранённый файл не прошёл проверку") from exc
    return Response(data, media_type=mime, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/assets/{kind}")
async def get_asset(kind: Literal["photo", "video"], c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner access required")
    path = asset(c, kind)
    if path is None:
        raise HTTPException(404, "Reference asset has not been supplied")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@router.post("/chat")
async def chat(body: studio_chat.ChatIn, c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner subscription access required")
    try:
        studio_chat.validate(body)
        return await studio_chat.generate(c, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except studio_chat.StudioModelUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, "Broker request failed; no fallback was attempted") from exc


@router.post("/chat-operations/{key}", status_code=202)
async def enqueue_chat(key: str, body: studio_chat.ChatIn, c: App, s: Db, p: Owner):
    try:
        await studio_chat.start(c, s, p.user, key, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except preview_operations.PreviewDisabled as exc:
        raise HTTPException(423, "Studio text queue disabled") from exc
    except preview_operations.PreviewCapacity as exc:
        raise HTTPException(429, {"status": "queue_full", "scope": str(exc)}) from exc
    except preview_operations.PreviewConflict as exc:
        raise HTTPException(409, "Studio operation ID already used") from exc
    return {"id": key}


@router.get("/chat-operations/{key}")
async def chat_status(key: str, c: App, s: Db, p: Owner, response: Response):
    headers = {"Cache-Control": "private, no-store"}
    response.headers.update(headers)
    scope = studio_chat.binding(c, p.user)
    if scope is None:
        raise HTTPException(403, "Personal owner subscription access required", headers=headers)
    row = await s.get(studio_chat.StudioChatOperation, key)
    if row is None or row.owner_id != p.user.id or row.binding != scope:
        raise HTTPException(404, "Studio operation not found", headers=headers)
    if row.expires_at <= c.clock():
        return {"status": "expired"}
    job = await s.get(Job, row.job_id)
    if job is None or job.status in ("failed", "cancelled"):
        return {"status": "unknown"}
    if job.status != "done":
        return {"status": job.status}
    if not row.result_cipher:
        return {"status": "unavailable"}
    return {"status": "complete", "result": c.box.decrypt_json(row.result_cipher)}
