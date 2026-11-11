"""Private owner media studio. No paid fallback or direct provider credentials."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from sim.api.deps import App, Owner
from sim.ports.llm import LLMError

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
    detail = "Google speech/video are not connected in Broker. No generation has been performed."
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
        "detail": detail,
    }


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
