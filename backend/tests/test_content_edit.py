"""An operator cannot slip unapproved text into an approved post."""
from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import Job
from sim.services import jobkinds
from tests.conftest import OPERATOR, OWNER, login


async def publish_jobs(container):
    async with container.factory() as s:
        rows = (await s.scalars(select(Job).where(Job.kind == jobkinds.CONTENT_PUBLISH))).all()
        return [(j.status, j.payload["post_id"]) for j in rows]


async def approved_post(api, world, caption="одобрено"):
    csrf = await login(api, *OWNER)
    h = {"X-CSRF-Token": csrf}
    body = {"persona_id": world.persona.id, "kind": "photo", "caption": caption,
            "scheduled_at": "2030-01-01T10:00:00Z"}
    post = (await api.post("/api/content", json=body, headers=h)).json()
    done = (await api.post(f"/api/content/{post['id']}/approve", headers=h)).json()
    assert done["status"] == "scheduled"
    return post["id"], body, h


async def test_operator_edit_of_scheduled_post_resets_to_draft(api, container, world):
    post_id, body, _ = await approved_post(api, world)
    await api.post("/api/auth/logout")
    op = {"X-CSRF-Token": await login(api, *OPERATOR)}
    resp = await api.put(f"/api/content/{post_id}", json={**body, "caption": "подмена"}, headers=op)
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft" and resp.json()["caption"] == "подмена"
    assert await publish_jobs(container) == [("cancelled", post_id)]  # nothing will be published


async def test_owner_edit_keeps_scheduled_status(api, container, world):
    post_id, body, h = await approved_post(api, world)
    resp = await api.put(f"/api/content/{post_id}", json={**body, "caption": "правка"}, headers=h)
    assert resp.json()["status"] == "scheduled"
    assert [s for s, _ in await publish_jobs(container)].count("pending") == 1
