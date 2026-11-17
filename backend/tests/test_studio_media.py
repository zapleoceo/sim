"""Synthetic ports only. Production Studio cannot enable these operations."""
import asyncio
from uuid import uuid4

import pytest
from conftest import OWNER, login
from sqlalchemy import select

from sim.adapters.db.models import AdminUser, TraceEvent
from sim.ports.speech import SpeechError, SpeechResult
from sim.ports.video import VideoResult
from sim.services.studio_media import (
    MediaRequest,
    StudioConflict,
    StudioMedia,
    StudioOperation,
    StudioUnavailable,
)


class FakeMedia:
    def __init__(self):
        self.calls = []
        self.error = None

    async def synthesize(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.error:
            raise self.error
        return SpeechResult(b"synthetic-audio", "audio/wav")

    async def generate_video(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return VideoResult(b"synthetic-video", "video/mp4")


@pytest.fixture
async def media_setup(api, container):
    await login(api, OWNER[0])
    async with container.factory() as session:
        owner_id = await session.scalar(select(AdminUser.id).where(AdminUser.telegram_id == OWNER[0]))
    fake = FakeMedia()
    service = StudioMedia(container.factory, container.box, fake, fake, enabled=True)
    return service, fake, owner_id


@pytest.mark.parametrize("kind", ["speech", "video"])
async def test_completed_receipt_reused_after_restart(media_setup, container, kind):
    service, fake, owner_id = media_setup
    request = MediaRequest(operation_id=uuid4(), kind=kind, text="Synthetic fixture")
    row = await service.execute(owner_id, request)
    assert row.status == "complete"
    assert "synthetic-" not in row.result_encrypted
    assert service.artifact(row)[0].startswith(b"synthetic-")
    restarted = StudioMedia(container.factory, container.box, fake, fake, enabled=True)
    assert (await restarted.execute(owner_id, request)).id == row.id
    assert len(fake.calls) == 1
    assert await restarted.get(owner_id + 1000, row.id) is None
    with pytest.raises(StudioConflict):
        await restarted.execute(owner_id, request.model_copy(update={"text": "Changed"}))
    assert len(fake.calls) == 1


@pytest.mark.parametrize("error", [SpeechError("secret provider detail"), asyncio.CancelledError()])
async def test_unknown_blocks_same_and_new_id_after_restart(media_setup, container, error):
    service, fake, owner_id = media_setup
    request = MediaRequest(operation_id=uuid4(), kind="speech", text="Synthetic fixture")
    fake.error = error
    with pytest.raises(type(error)):
        await service.execute(owner_id, request)
    restarted = StudioMedia(container.factory, container.box, fake, fake, enabled=True)
    assert (await restarted.execute(owner_id, request)).status == "unknown"
    with pytest.raises(StudioConflict, match="outcome unknown"):
        await restarted.execute(owner_id, request.model_copy(update={"operation_id": uuid4()}))
    assert len(fake.calls) == 1
    async with container.factory() as session:
        row = await session.get(StudioOperation, str(request.operation_id))
        assert row.result_encrypted is None


async def test_default_disabled_even_with_valid_ports(media_setup, container):
    _, fake, owner_id = media_setup
    service = StudioMedia(container.factory, container.box, fake, fake)
    with pytest.raises(StudioUnavailable):
        await service.execute(owner_id, MediaRequest(operation_id=uuid4(), kind="speech", text="Hi"))
    assert fake.calls == []


async def test_disabled_api_and_private_status(api, container):
    token = await login(api, OWNER[0])
    container.settings.studio_owner_telegram_id = OWNER[0]
    fake = FakeMedia()
    container.speech = container.video = fake
    body = {"operation_id": str(uuid4()), "kind": "speech", "text": "Synthetic"}
    assert (await api.post("/api/studio/media", json=body)).status_code == 403
    response = await api.post("/api/studio/media", headers={"X-CSRF-Token": token}, json=body)
    assert response.status_code == 423
    assert fake.calls == []
    state = (await api.get("/api/studio")).json()
    assert state["capabilities_verified"] is False
    assert state["speech_available"] is False and state["video_available"] is False
    async with container.factory() as session:
        events = list(await session.scalars(select(TraceEvent).where(
            TraceEvent.name == "POST /api/studio/media")))
        assert events
        for event in events:
            assert event.detail["request_body"] == {
                "omitted": "Тело приватной медиаоперации не сохраняется"}
    container.settings.studio_owner_telegram_id = OWNER[0] + 1
    for suffix in ("", "/artifact"):
        assert (await api.get(f'/api/studio/media/{body["operation_id"]}{suffix}')).status_code == 403
