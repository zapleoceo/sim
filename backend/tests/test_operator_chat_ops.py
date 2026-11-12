"""Operator chat actions use real queue paths and remain idempotent."""
from __future__ import annotations

from sqlalchemy import select

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Dialog, Job, Message
from sim.adapters.db.session import transaction
from sim.domain.enums import Author, Delivery
from sim.ports.channel import SendResult
from sim.services import jobkinds
from tests.conftest import OWNER, inbound, login
from tests.test_ingest import ingest


async def dialog_id(container, world) -> int:
    await ingest(container, world.channel.id, inbound("Привет"))
    async with container.factory() as session:
        return (await session.scalars(select(Dialog))).unique().one().id


async def test_manual_text_retries_return_one_message_and_one_send_job(api, container, world):
    did = await dialog_id(container, world)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    first = await api.post(
        f"/api/dialogs/{did}/messages", json={"text": "Отвечу я", "request_id": "req-1"}, headers=h,
    )
    again = await api.post(
        f"/api/dialogs/{did}/messages", json={"text": "Отвечу я", "request_id": "req-1"}, headers=h,
    )
    conflict = await api.post(
        f"/api/dialogs/{did}/messages", json={"text": "Другой текст", "request_id": "req-1"}, headers=h,
    )
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert conflict.status_code == 409
    async with container.factory() as session:
        messages = (await session.scalars(select(Message).where(Message.author == Author.OPERATOR))).all()
        sends = (await session.scalars(select(Job).where(Job.kind == jobkinds.SEND))).all()
        dialog = await session.get(Dialog, did)
    assert len(messages) == len(sends) == 1
    assert dialog.ai_paused and not dialog.needs_operator


async def test_operator_voice_queues_existing_channel_voice_path(api, container, world, connector):
    did = await dialog_id(container, world)
    connector.port.capabilities = frozenset({*connector.port.capabilities, Capability.SEND_VOICE})
    class Encoder:
        async def to_voice_note(self, audio: bytes, ambience: str = "off") -> bytes:
            assert audio == b"recorded audio"
            return b"OggS-voice"
    container.encoder = Encoder()
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    response = await api.post(
        f"/api/dialogs/{did}/voice", headers=h,
        data={"request_id": "voice-1"},
        files={"file": ("voice.webm", b"recorded audio", "audio/webm")},
    )
    assert response.status_code == 201
    message_id = response.json()["id"]
    assert response.json()["media_kind"] == "voice"
    async with transaction(container.factory) as session:
        await container.outbound(session).deliver(message_id)
    assert connector.port.voices == [("chat-1", b"OggS-voice", None)]
    async with container.factory() as session:
        assert (await session.get(Message, message_id)).delivery is Delivery.SENT


async def test_attachment_support_is_explicit_and_uses_same_send_fence(api, container, world, connector):
    did = await dialog_id(container, world)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    unsupported = await api.post(
        f"/api/dialogs/{did}/attachments", headers=h,
        files={"file": ("pic.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )
    assert unsupported.status_code == 422
    sent: list[tuple[str, bytes, str, str, str]] = []
    async def send_attachment(thread: str, data: bytes, name: str, mime: str, caption: str):
        sent.append((thread, data, name, mime, caption))
        return SendResult(ok=True, external_message_id="attachment-1")
    connector.port.capabilities = frozenset({*connector.port.capabilities, Capability.SEND_ATTACHMENT})
    connector.port.send_attachment = send_attachment
    response = await api.post(
        f"/api/dialogs/{did}/attachments", headers=h, data={"caption": "Смотрите", "request_id": "file-1"},
        files={"file": ("pic.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )
    assert response.status_code == 201
    message_id = response.json()["id"]
    async with transaction(container.factory) as session:
        await container.outbound(session).deliver(message_id)
    assert len(sent) == 1 and sent[0][0] == "chat-1"
    assert sent[0][1].startswith(b"\x89PNG") and sent[0][3] == "image/png"
    assert sent[0][4] == "Смотрите"
