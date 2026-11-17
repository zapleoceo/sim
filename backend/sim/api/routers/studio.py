"""Private owner media studio. No paid fallback or direct provider credentials."""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from sim.adapters.llm.broker import BrokerLLM
from sim.adapters.studio_broker import StudioBroker
from sim.api.deps import App, Owner
from sim.ports.llm import LLMError
from sim.ports.speech import SpeechError
from sim.ports.video import VideoError
from sim.services.studio_media import (
    MediaRequest,
    StudioConflict,
    StudioMedia,
    StudioUnavailable,
)

router = APIRouter(prefix="/api/studio", tags=["studio"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value


class ChatIn(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    messages: list[Turn] = Field(min_length=1, max_length=24)


def personal_owner(c: App, p: Owner) -> bool:
    return bool(c.settings.studio_owner_telegram_id) and (
        p.user.telegram_id == c.settings.studio_owner_telegram_id
    )


def asset(c: App, kind: str) -> Path | None:
    name = (c.settings.studio_photo_filename if kind == "photo"
            else c.settings.studio_video_filename)
    root = (Path(c.settings.media_dir) / "studio").resolve()
    path = (root / name).resolve()
    allowed = {".jpg", ".jpeg", ".png", ".webp"} if kind == "photo" else {".mp4", ".webm"}
    if not name or not path.is_relative_to(root) or path.suffix.lower() not in allowed:
        return None
    return path if path.is_file() else None


async def subscription_models(c: App) -> list[str]:
    rows = await c.llm.list_models()
    return sorted({
        str(row["id"]) for row in rows
        if row.get("available") is True and row.get("tested") is True
        and row.get("billing_type") == "subscription"
        and (str(row.get("id", "")).startswith("claude-sub/claude-sonnet-")
             or str(row.get("id", "")).startswith("codex-sub/gpt-")
             and str(row.get("id", "")).endswith("-sol"))
    })


@router.get("")
async def status(c: App, p: Owner):
    personal = personal_owner(c, p)
    models: list[str] = []
    detail = "Генерация речи и видео в Studio выключена. Возможности и права Broker для неё не проверены."
    if not personal:
        detail += " Personal subscription access is not configured for this account."
    else:
        try:
            models = await subscription_models(c)
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
        "detail": detail,
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
    row = await media_service(c).get(p.user.id, str(operation_id))
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
async def chat(body: ChatIn, c: App, p: Owner):
    if not personal_owner(c, p):
        raise HTTPException(403, "Personal owner subscription access required")
    if body.messages[-1].role != "user":
        raise HTTPException(422, "The final message must be from the user")
    if sum(len(turn.content) for turn in body.messages) > 32000:
        raise HTTPException(422, "Conversation exceeds the studio limit")
    try:
        models = await subscription_models(c)
        if body.model not in models:
            raise HTTPException(409, "Selected subscription model is unavailable or unverified")
        result = await c.llm.chat(
            [{"role": "system", "content": (
                "Ты виртуальный персонаж в личной творческой студии владельца. "
                "Отвечай разговорно на языке пользователя или помогай писать промпты для медиа. "
                "Не утверждай, что видел фото, создал медиа или выполнил действия. "
                "К этому текстовому разговору не приложены изображения или видео."
            )}, *[turn.model_dump() for turn in body.messages]],
            capability="chat:fast", model=body.model, workflow="studio",
        )
    except LLMError as exc:
        raise HTTPException(502, "Broker request failed; no fallback was attempted") from exc
    return {"text": result.text, "model": result.model or body.model}
