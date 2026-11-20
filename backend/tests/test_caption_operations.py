"""Caption jobs use private, revision-bound suggestions and never mutate a post."""
from __future__ import annotations

from datetime import UTC
from uuid import uuid4

from sqlalchemy import func, select

from sim.adapters.db.models import CaptionOperationAlias
from sim.ports.llm import LLMError
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, OWNER, login


def path(container, post):
    millis = int(container.clock().replace(tzinfo=UTC).timestamp() * 1000)
    return f"/api/content/{post}/caption-operations/{millis}-{uuid4()}"


async def draft(api, container, world):
    container.settings.preview_async_enabled = True
    auth = {"X-CSRF-Token": await login(api, *OWNER)}
    body = {"persona_id": world.persona.id, "kind": "reel", "caption": "Исходная идея"}
    post = (await api.post("/api/content", json=body, headers=auth)).json()
    return auth, body, post["id"]


async def test_caption_queue_returns_private_suggestion_without_overwrite(api, container, world, fake_llm):
    auth, body, post = await draft(api, container, world)
    operation = path(container, post)
    assert (await api.post(f"/api/content/{post}/generate-caption", headers=auth)).status_code == 428
    assert (await api.post(operation, headers=auth)).status_code == 202
    assert (await api.post(operation, headers=auth)).status_code == 202
    alias = path(container, post)
    first_id = operation.rsplit("/", 1)[1]
    assert (await api.post(alias, headers=auth)).json() == {"id": first_id}
    assert (await api.get(alias)).json() == {"status": "pending"}
    assert (await api.get(operation)).json() == {"status": "pending"}
    assert not fake_llm.calls

    fake_llm.replies = ["Новая подпись #сцена"]
    assert await Worker(container).tick() == 1
    result = await api.get(operation)
    assert result.headers["cache-control"] == "no-store"
    assert result.json() == {"id": first_id, "status": "complete", "result": {"caption": "Новая подпись #сцена"}}
    late_tab = path(container, post)
    assert (await api.post(late_tab, headers=auth)).json() == {"id": first_id}
    assert (await api.get(late_tab)).json() == result.json()
    assert (await api.get(alias)).json() == result.json()
    assert await Worker(container).tick() == 0
    assert len(fake_llm.calls) == 1
    posts = (await api.get("/api/content")).json()["items"]
    assert next(item for item in posts if item["id"] == post)["caption"] == body["caption"]
    assert (await api.get(operation.replace(f"/content/{post}/", "/content/999999/"))).status_code == 404
    key = operation.rsplit("/", 1)[1]
    assert (await api.get(f"/api/personas/{world.persona.id}/preview-operations/{key}")).status_code == 404
    await login(api, *OPERATOR)
    assert (await api.get(operation)).status_code == 404


async def test_caption_stale_queued_draft_never_calls_broker(api, container, world, fake_llm):
    auth, body, post = await draft(api, container, world)
    operation = path(container, post)
    assert (await api.post(operation, headers=auth)).status_code == 202
    changed = {**body, "caption": "Правка редактора"}
    assert (await api.put(f"/api/content/{post}", json=changed, headers=auth)).status_code == 200
    assert await Worker(container).tick() == 1
    assert (await api.get(operation)).json() == {"status": "unavailable"}
    assert not fake_llm.calls


async def test_caption_unknown_outcome_is_retained_for_a_different_key(api, container, world, fake_llm):
    auth, _, post = await draft(api, container, world)
    operation = path(container, post)
    fake_llm.replies = [LLMError("synthetic missing provider acknowledgement")]
    assert (await api.post(operation, headers=auth)).status_code == 202
    assert await Worker(container).tick() == 1
    assert (await api.get(operation)).json() == {"status": "unknown"}
    retry = path(container, post)
    assert (await api.post(retry, headers=auth)).json() == {"id": operation.rsplit("/", 1)[1]}
    assert (await api.get(retry)).json() == {"status": "unknown"}
    assert await Worker(container).tick() == 0
    assert len(fake_llm.calls) == 1


async def test_caption_alias_bound_and_disabled_queue_do_not_grow(api, container, world):
    auth, _, post = await draft(api, container, world)
    operation = path(container, post)
    canonical = operation.rsplit("/", 1)[1]
    assert (await api.post(operation, headers=auth)).status_code == 202
    existing_alias = path(container, post)
    assert (await api.post(existing_alias, headers=auth)).json() == {"id": canonical}

    container.settings.preview_async_enabled = False
    while_disabled = path(container, post)
    assert (await api.post(while_disabled, headers=auth)).json() == {"id": canonical}
    assert (await api.get(while_disabled)).status_code == 404
    assert (await api.get(existing_alias)).json() == {"status": "pending"}

    container.settings.preview_async_enabled = True
    for _ in range(7):
        alias = path(container, post)
        assert (await api.post(alias, headers=auth)).json() == {"id": canonical}
        assert (await api.get(alias)).json() == {"status": "pending"}
    over_limit = path(container, post)
    assert (await api.post(over_limit, headers=auth)).json() == {"id": canonical}
    assert (await api.get(over_limit)).status_code == 404
    assert (await api.post(existing_alias, headers=auth)).json() == {"id": canonical}
    async with container.factory() as session:
        assert await session.scalar(select(func.count()).select_from(CaptionOperationAlias)) == 8
