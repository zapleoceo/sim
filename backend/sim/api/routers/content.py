from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from sim.adapters.db.models import ContentPost, ContentPublishIntent, Job
from sim.adapters.db.repositories.records import ContentRepo
from sim.api.deps import App, Authed, Db, authed, owner_only
from sim.api.schemas import ContentIn, content_out, dt
from sim.services import preview_operations

# ContentError → 422, ContentConflict → 409, LLMError → 502: api/errors.py.
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
    return content_out(await c.content(s).create(**body.model_dump()))


@router.put("/{post_id}")
async def update_post(post_id: int, body: ContentIn, c: App, s: Db, p: Authed):
    post = await post_or_404(s, post_id)
    updated = await c.content(s).update(
        post, by_owner=p.user.role.has_owner_rights, **body.model_dump()
    )
    return content_out(updated)


@router.delete("/{post_id}", status_code=204)
async def delete_post(post_id: int, s: Db) -> Response:
    await ContentRepo(s).delete(await post_or_404(s, post_id))
    return Response(status_code=204)


@router.post("/{post_id}/approve", dependencies=[Depends(owner_only)])
async def approve_post(post_id: int, c: App, s: Db):
    post = await post_or_404(s, post_id)
    return content_out(await c.content(s).approve(post))


@router.post("/{post_id}/generate-caption")
async def generate_caption(post_id: int, s: Db):
    await post_or_404(s, post_id)
    raise HTTPException(428, "Обновите страницу для генерации подписи через очередь")


@router.post("/{post_id}/caption-operations/{key}", status_code=202)
async def enqueue_caption(post_id: int, key: str, c: App, s: Db, p: Authed):
    post = await post_or_404(s, post_id)
    try:
        row = await preview_operations.start_caption(c, s, p.user.id, post, key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except preview_operations.PreviewDisabled as exc:
        raise HTTPException(404, {"status": "queue_disabled"}) from exc
    except preview_operations.PreviewCapacity as exc:
        raise HTTPException(429, {"status": "queue_full", "scope": str(exc)}) from exc
    except preview_operations.PreviewConflict as exc:
        raise HTTPException(409, "Caption request ID already used") from exc
    return {"id": row.id}


@router.get("/{post_id}/caption-operations/{key}")
async def caption_status(post_id: int, key: str, c: App, s: Db, p: Authed, response: Response):
    response.headers["Cache-Control"] = "no-store"
    row = await preview_operations.scoped(s, p.user.id, post_id, key, caption=True)
    if row is None:
        if await preview_operations.definitively_missing(
            s, p.user.id, post_id, key, c.clock(), caption=True,
        ):
            return {"status": "expired"}
        row = await preview_operations.scoped(s, p.user.id, post_id, key, caption=True)
        if row is None:
            raise HTTPException(404, "Caption operation not found", headers={"Cache-Control": "no-store"})
    if row.expires_at <= c.clock():
        return {"status": "expired"}
    job = await s.get(Job, row.job_id)
    if job is not None and job.last_error == "preview_disabled":
        return {"status": "unavailable"}
    if job is None or job.status in ("failed", "cancelled"):
        return {"status": "unknown"}
    if job.status != "done":
        return {"status": job.status}
    post = await s.get(ContentPost, post_id)
    if not row.result_cipher or post is None or row.revision != await preview_operations.caption_revision(c, s, post):
        return {"status": "unavailable"}
    return {"id": row.id, "status": "complete", "result": c.box.decrypt_json(row.result_cipher)}


@router.get("/{post_id}/publication", dependencies=[Depends(owner_only)])
async def publication_state(post_id: int, s: Db):
    """Read-only history, including retained intents for deleted posts. Never resets a fence."""
    intent = await s.get(ContentPublishIntent, post_id)
    if intent is None:
        await post_or_404(s, post_id)
        return {"post_id": post_id, "state": "not_attempted"}
    return {
        "post_id": post_id,
        "state": "receipt_known" if intent.external_id else "uncertain",
        "attempted_at": dt(intent.created_at),
        "external_id": intent.external_id,
        "published_at": dt(intent.published_at),
    }
