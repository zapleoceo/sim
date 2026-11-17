"""Synthetic ports only. Production Studio cannot enable these operations."""
import asyncio
from uuid import uuid4

import pytest
from conftest import OWNER, login
from sqlalchemy import inspect, select

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
from tests.studio_fixtures import mp4_bytes, wav_bytes


class FakeMedia:
    def __init__(self):
        self.calls = []
        self.error = None

    async def synthesize(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.error:
            raise self.error
        return SpeechResult(wav_bytes(), "audio/wav")

    async def generate_video(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return VideoResult(mp4_bytes(), "video/mp4")


@pytest.fixture
async def media_setup(api, container):
    token = await login(api, OWNER[0])
    api.headers["X-CSRF-Token"] = token
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
    assert service.artifact(row)[0] == (wav_bytes() if kind == "speech" else mp4_bytes())
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


async def test_known_remote_receipt_reconciles_without_post(media_setup, container):
    _, fake, owner_id = media_setup

    class Remote:
        scope, submits, reads, ready = "synthetic-scope", 0, 0, False

        async def submit_video(self, prompt):
            self.submits += 1
            return "42"

        async def read_video(self, remote_id):
            assert remote_id == "42"
            self.reads += 1
            return VideoResult(mp4_bytes(), "video/mp4") if self.ready else None

    remote = Remote()
    service = StudioMedia(container.factory, container.box, fake, None, enabled=True, remote=remote)
    request = MediaRequest(operation_id=uuid4(), kind="video", text="Synthetic fixture")
    row = await service.execute(owner_id, request)
    assert row.status == "unknown" and remote.submits == 1
    assert container.box.decrypt_json(row.result_encrypted)["remote_id"] == "42"
    restarted = StudioMedia(container.factory, container.box, fake, None, enabled=True, remote=remote)
    remote.scope = "different-project-key"
    with pytest.raises(StudioConflict, match="matching retained"):
        await restarted.reconcile(owner_id, row.id)
    assert remote.reads == 1
    remote.scope, remote.ready = "synthetic-scope", True
    assert (await restarted.reconcile(owner_id, row.id)).status == "complete"
    assert (await restarted.reconcile(owner_id, row.id)).status == "complete"
    assert remote.submits == 1 and remote.reads == 2
    history = await restarted.recent(owner_id)
    assert len(history) == 1
    assert "result_encrypted" in inspect(history[0]).unloaded
    status = await restarted.get(owner_id, row.id, include_artifact=False)
    assert "result_encrypted" in inspect(status).unloaded
    assert await restarted.recent(owner_id + 1000) == []


async def test_unknown_without_receipt_cannot_reconcile(media_setup):
    service, fake, owner_id = media_setup
    fake.error = SpeechError("lost response")
    request = MediaRequest(operation_id=uuid4(), kind="speech", text="Synthetic")
    with pytest.raises(SpeechError):
        await service.execute(owner_id, request)
    with pytest.raises(StudioConflict, match="matching retained"):
        await service.reconcile(owner_id, str(request.operation_id))
    assert len(fake.calls) == 1


async def test_lost_video_submit_receipt_never_reposts(media_setup, container):
    from sim.ports.video import VideoError

    _, fake, owner_id = media_setup

    class LostReceipt:
        scope, submits = "synthetic-scope", 0

        async def submit_video(self, prompt):
            self.submits += 1
            raise VideoError("accepted remotely, response lost")

        async def read_video(self, remote_id):
            raise AssertionError("No retained ID: must not guess a job")

    remote = LostReceipt()
    service = StudioMedia(container.factory, container.box, fake, None, enabled=True, remote=remote)
    request = MediaRequest(operation_id=uuid4(), kind="video", text="Synthetic")
    with pytest.raises(VideoError):
        await service.execute(owner_id, request)
    assert (await service.execute(owner_id, request)).status == "unknown"
    with pytest.raises(StudioConflict):
        await service.reconcile(owner_id, str(request.operation_id))
    with pytest.raises(StudioConflict):
        await service.execute(owner_id, request.model_copy(update={"operation_id": uuid4()}))
    assert remote.submits == 1


async def test_history_and_artifacts_are_owner_bound_and_private(media_setup, api, container):
    service, _, owner_id = media_setup
    container.settings.studio_owner_telegram_id = OWNER[0]
    request = MediaRequest(operation_id=uuid4(), kind="speech", text="private synthetic text")
    row = await service.execute(owner_id, request)
    listing = await api.get("/api/studio/media")
    assert listing.status_code == 200
    assert listing.json()["operations"][0]["operation_id"] == row.id
    assert "private synthetic text" not in listing.text and "encrypted" not in listing.text
    result = await api.get(f"/api/studio/media/{row.id}/artifact")
    assert result.content == wav_bytes()
    assert result.headers["cache-control"] == "private, no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    # Reuse the fixture session. Re-signing with the wall-clock/counter helper can
    # produce the SAME auth_date/hash when the wall second advances by one.
    # Production correctly rejects that replay; this test is not testing re-login.
    assert (await api.post(f"/api/studio/media/{row.id}/reconcile")).status_code == 423
    container.settings.studio_owner_telegram_id = OWNER[0] + 1
    assert (await api.get("/api/studio/media")).status_code == 403
    assert (await api.get(f"/api/studio/media/{row.id}/artifact")).status_code == 403
