"""Attachments: downloaded through the channel port in a `media` job, not in the webhook."""
from __future__ import annotations

from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from sim.adapters.db.models import Channel, Dialog, Job, Message
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.connectors.telegram import TelegramConnector
from sim.domain.enums import ChannelKind
from sim.services import jobkinds
from sim.services.media import VOICE_FAILED
from sim.worker.handlers import HANDLERS
from sim.worker.loop import Worker
from tests.conftest import decision_json, inbound
from tests.test_ingest import ingest

REF = "tg-file:VOICE1"
WAITS = (jobkinds.MEDIA, jobkinds.REPLY)   # follow-ups / sends are not part of these scenarios


async def make_due(container):
    async with transaction(container.factory) as s:
        q = select(Job).where(Job.status == "pending", Job.kind.in_(WAITS))
        for job in (await s.scalars(q)).all():
            job.run_at = utcnow() - timedelta(seconds=1)


async def jobs_of(container, kind):
    async with container.factory() as s:
        return (await s.scalars(select(Job).where(Job.kind == kind))).all()


async def ai_texts(container):
    async with container.factory() as s:
        rows = (await s.scalars(select(Message).where(Message.author == "ai"))).all()
        return [m.text for m in rows]


async def drain(container, worker, rounds=12):
    """Tick (skipping every wait) until no pending job is left."""
    for _ in range(rounds):
        await make_due(container)
        if await worker.tick() == 0:
            return


async def ingest_voice(container, world, ext="v1", ref=REF):
    return await ingest(
        container, world.channel.id, inbound("", ext=ext, media_kind="voice", media_url=ref)
    )


async def test_webhook_does_not_download_it_queues_a_media_job(container, world, connector):
    res = await ingest_voice(container, world)
    assert connector.port.downloads == []                      # nothing fetched in the request
    assert res.message.text == "[голосовое сообщение]" and res.message.media_url is None
    [job] = await jobs_of(container, jobkinds.MEDIA)
    assert job.payload["ref"] == REF and job.payload["transcribe"] is True
    assert job.payload["message_id"] == res.message.id
    assert len(await jobs_of(container, jobkinds.REPLY)) == 1


async def test_media_job_downloads_via_port_and_transcribes(container, world, fake_llm, connector):
    fake_llm.transcript = "расскажите про курс"
    res = await ingest_voice(container, world)
    worker = Worker(container, {jobkinds.MEDIA: HANDLERS[jobkinds.MEDIA]})
    await make_due(container)
    await worker.tick()
    assert connector.port.downloads == [REF]                   # the marker went to the port
    async with container.factory() as s:
        message = await s.get(Message, res.message.id)
        assert message.text == "расскажите про курс"
        assert message.media_url.startswith("/api/media/")
        dialog = (await s.scalars(select(Dialog))).unique().one()
        assert dialog.last_message_text == "расскажите про курс"


async def test_reply_waits_for_the_transcript(container, world, fake_llm, connector):
    fake_llm.transcript = "хочу записаться"
    fake_llm.replies = [decision_json(reply="Отлично!")]
    await ingest_voice(container, world)
    worker = Worker(container)
    await drain(container, worker)
    assert await ai_texts(container) == ["Отлично!"]
    prompt = fake_llm.calls[0][0]
    assert any("хочу записаться" in m["content"] for m in prompt if m["role"] == "user")
    assert not any("[голосовое сообщение]" in m["content"] for m in prompt)


async def test_reply_job_does_not_run_the_model_while_media_is_open(container, world, fake_llm):
    await ingest_voice(container, world)
    await make_due(container)
    async with transaction(container.factory) as s:                    # lease like the worker (the media job is leased too: still open)
        claimed = await JobRepo(s).claim(utcnow(), 900)
        reply = next(j for j in claimed if j.kind == jobkinds.REPLY)
        reply_id, payload = reply.id, dict(reply.payload)
    worker = Worker(container)
    await worker.run_job(reply_id, jobkinds.REPLY, payload)
    assert fake_llm.calls == []
    pending = [j for j in await jobs_of(container, jobkinds.REPLY) if j.status == "pending"]
    assert len(pending) == 1                                            # re-queued, not lost


async def test_download_failure_ends_with_placeholder_and_reply_continues(
    container, world, fake_llm, connector
):
    connector.port.media_error = httpx.ConnectError("nope")
    fake_llm.replies = [decision_json(reply="Не расслышала, напишите текстом?")]
    res = await ingest_voice(container, world)
    worker = Worker(container)
    await drain(container, worker, rounds=20)
    assert len(connector.port.downloads) == 3                  # MEDIA_MAX_ATTEMPTS
    async with container.factory() as s:
        message = await s.get(Message, res.message.id)
        assert message.text == VOICE_FAILED and message.media_url is None
    assert await ai_texts(container) == ["Не расслышала, напишите текстом?"]


async def test_channel_without_download_capability_gets_placeholder(
    container, world, fake_llm, connector
):
    connector.port.capabilities = frozenset()
    res = await ingest_voice(container, world)
    await drain(container, Worker(container), rounds=20)
    async with container.factory() as s:
        assert (await s.get(Message, res.message.id)).text == VOICE_FAILED
    assert connector.port.downloads == []


async def test_image_is_stored_and_keeps_placeholder_text(container, world, connector):
    res = await ingest(
        container, world.channel.id,
        inbound("", ext="p1", media_kind="image", media_url="tg-file:PHOTO"),
    )
    worker = Worker(container)
    await drain(container, worker)
    async with container.factory() as s:
        message = await s.get(Message, res.message.id)
        assert message.text == "[изображение]" and message.media_url.startswith("/api/media/")
    [job] = await jobs_of(container, jobkinds.MEDIA)
    assert job.payload["transcribe"] is False


@respx.mock
async def test_real_telegram_adapter_voice_end_to_end(container, world, fake_llm):
    """The reviewer's scenario: a tg-file marker must reach getFile, not httpx.get."""
    container.registry = {ChannelKind.TELEGRAM: TelegramConnector()}
    async with transaction(container.factory) as s:
        channel = await s.get(Channel, world.channel.id)
        channel.session_enc = container.box.encrypt_json({"bot_token": "TOKEN"})
    api = "https://api.telegram.org"
    respx.post(f"{api}/botTOKEN/getFile").mock(return_value=httpx.Response(
        200, json={"ok": True, "result": {"file_path": "voice/a.oga", "file_size": 3}}))
    respx.get(f"{api}/file/botTOKEN/voice/a.oga").mock(return_value=httpx.Response(200, content=b"abc"))
    fake_llm.transcript = "привет, это голос"
    update = {"message": {"message_id": 5, "date": 1_700_000_000, "voice": {"file_id": "V1"},
                          "chat": {"id": 42, "type": "private"},
                          "from": {"id": 42, "first_name": "Анна"}}}
    [msg] = TelegramConnector().parse_webhook(update)
    assert msg.media_url == "tg-file:V1"
    await ingest(container, world.channel.id, msg)
    await drain(container, Worker(container))
    async with container.factory() as s:
        texts = [m.text for m in (await s.scalars(select(Message).order_by(Message.id))).all()]
    assert "привет, это голос" in texts and VOICE_FAILED not in texts
