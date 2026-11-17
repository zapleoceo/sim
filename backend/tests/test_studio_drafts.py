"""Draft persistence never needs an enabled Studio or any provider call."""
from unittest.mock import AsyncMock

import pytest
from conftest import OWNER, login
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from sim.adapters.db.models import TraceEvent
from sim.services.studio_drafts import StudioDraft, StudioDrafts
from sim.services.studio_media import StudioOperation

PATH = "/api/studio/media/draft/video"


@pytest.fixture
async def draft_api(api, container):
    api.headers["X-CSRF-Token"] = await login(api, OWNER[0])
    container.settings.studio_owner_telegram_id = OWNER[0]
    return api


async def test_reload_cas_delete_and_encryption_without_operations(draft_api, container):
    first = (await draft_api.get(PATH)).json()
    assert first["text"] == "" and first["revision"] == 0
    response = await draft_api.put(PATH, json={**first, "text": "Private synthetic draft"})
    assert response.status_code == 200
    saved = response.json()
    assert (await draft_api.get(PATH)).json() == saved
    assert response.headers["cache-control"] == "private, no-store"
    assert (await draft_api.put(PATH, json=first)).status_code == 409
    updated = (await draft_api.put(PATH, json={**saved, "text": "Revised"})).json()
    cleared = (await draft_api.put(PATH, json={**updated, "text": ""})).json()
    assert cleared["text"] == "" and cleared["revision"] == 3
    assert (await draft_api.put(PATH, json=updated)).status_code == 409
    async with container.factory() as session:
        assert list(await session.scalars(select(StudioOperation))) == []
        rows = list(await session.scalars(select(StudioDraft)))
        assert len(rows) == 1 and rows[0].text_encrypted is None
        events = list(await session.scalars(select(TraceEvent)))
        assert "Private synthetic draft" not in str([e.detail for e in events])
    # Save again and verify encryption, using the same existing SecretBox.
    await draft_api.put(PATH, json={**cleared, "text": "Encrypted fixture"})
    async with container.factory() as session:
        row = await session.scalar(select(StudioDraft))
        assert "Encrypted fixture" not in row.text_encrypted
        assert container.box.decrypt(row.text_encrypted) == "Encrypted fixture"


async def test_reference_and_owner_changes_do_not_reuse_draft(draft_api, container):
    first = (await draft_api.get(PATH)).json()
    await draft_api.put(PATH, json={**first, "text": "Old character"})
    container.settings.studio_photo_filename = "another-character.jpg"
    fresh = (await draft_api.get(PATH)).json()
    assert fresh["scope"] != first["scope"] and fresh["text"] == ""
    assert (await draft_api.put(PATH, json=first)).status_code == 409
    container.settings.studio_owner_telegram_id = OWNER[0] + 1
    assert (await draft_api.get(PATH)).status_code == 403
    assert (await draft_api.put(PATH, json=fresh)).status_code == 403


async def test_csrf_bounds_and_storage_failure_keep_saved_draft(draft_api, container, monkeypatch):
    first = (await draft_api.get(PATH)).json()
    token = draft_api.headers.pop("X-CSRF-Token")
    assert (await draft_api.put(PATH, json=first)).status_code == 403
    draft_api.headers["X-CSRF-Token"] = token
    assert (await draft_api.put(PATH, json={**first, "text": "x" * 2001})).status_code == 422
    saved = (await draft_api.put(PATH, json={**first, "text": "Keep me"})).json()
    monkeypatch.setattr(StudioDrafts, "save", AsyncMock(side_effect=SQLAlchemyError("offline")))
    failed = await draft_api.put(PATH, json={**saved, "text": "Not saved"})
    assert failed.status_code == 503
    assert (await draft_api.get(PATH)).json() == saved


async def test_anonymous_draft_is_not_readable(api):
    assert (await api.get(PATH)).status_code == 401
