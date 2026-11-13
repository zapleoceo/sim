"""Domain errors map to HTTP statuses in api/errors.py, not in a try/except per route."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from sim.api.errors import STATUS_BY_ERROR, install_error_handlers
from sim.ports.llm import LLMError
from sim.services.channels import ChannelError
from sim.services.content import ContentConflict, ContentError
from sim.services.conversation import ConversationError
from sim.services.guard import GuardError
from sim.services.persona_media import MediaNotFound, MediaRejected, MediaTooLarge
from sim.services.preview_media import AttachmentError
from tests.conftest import OWNER, login


def app_raising(exc: Exception) -> httpx.AsyncClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise exc

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("exc,status", [
    (ChannelError("no connector"), 422),
    (ContentError("persona not found"), 422),
    (ContentConflict("a published post cannot be edited"), 409),   # subclass wins over its base
    (ConversationError("bad"), 422),
    (AttachmentError("empty"), 422),
    (MediaRejected("bad type"), 422),
    (MediaTooLarge("too large"), 413),
    (MediaNotFound("Media not found"), 404),
])
async def test_domain_error_status_and_detail(exc, status):
    async with app_raising(exc) as client:
        response = await client.get("/boom")
    assert response.status_code == status
    assert response.json() == {"detail": str(exc)}


@pytest.mark.parametrize("exc", [LLMError("timeout"), GuardError("same model")])
async def test_broker_errors_are_502(exc):
    async with app_raising(exc) as client:
        response = await client.get("/boom")
    assert response.status_code == 502
    assert response.json() == {"detail": f"Broker error: {exc}"}


def test_every_mapped_status_is_a_client_error():
    assert all(400 <= code < 500 for code in STATUS_BY_ERROR.values())


async def test_real_app_maps_channel_error(api):
    """The handlers are installed on the app itself: an unknown persona for a new channel."""
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    response = await api.post("/api/channels", json={"kind": "telegram", "persona_id": 9999}, headers=h)
    assert response.status_code == 422 and response.json() == {"detail": "persona not found"}
