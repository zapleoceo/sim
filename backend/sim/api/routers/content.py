from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from sim.adapters.db.models import ContentPost
from sim.adapters.db.repositories.records import ContentRepo
from sim.api.deps import App, Db, authed, owner_only
from sim.api.schemas import ContentIn, content_out
from sim.ports.llm import LLMError
from sim.services.content import ContentError

router = APIRouter(prefix="/api/content", tags=["content"], dependencies=[Depends(authed)])


async def post_or_404(s: Db, post_id: int) -> ContentPost:
    post = await ContentRepo(s).get(post_id)
    if post is None:
        raise HTTPException(404, "Post not found")
    return post


@router.get("")
async def list_posts(s: Db):
    return {"items": [content_out(p) for p in await ContentRepo(s).listing()]}


@router.post("", status_code=201)
async def create_post(body: ContentIn, c: App, s: Db):
    try:
        return content_out(await c.content(s).create(**body.model_dump()))
    except ContentError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{post_id}")
async def update_post(post_id: int, body: ContentIn, c: App, s: Db):
    post = await post_or_404(s, post_id)
    try:
        return content_out(await c.content(s).update(post, **body.model_dump()))
    except ContentError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{post_id}", status_code=204)
async def delete_post(post_id: int, s: Db) -> Response:
    await ContentRepo(s).delete(await post_or_404(s, post_id))
    return Response(status_code=204)


@router.post("/{post_id}/approve", dependencies=[Depends(owner_only)])
async def approve_post(post_id: int, c: App, s: Db):
    post = await post_or_404(s, post_id)
    try:
        return content_out(await c.content(s).approve(post))
    except ContentError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{post_id}/generate-caption")
async def generate_caption(post_id: int, c: App, s: Db):
    post = await post_or_404(s, post_id)
    try:
        return {"caption": await c.content(s).generate_caption(post)}
    except ContentError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"Broker error: {exc}") from exc
