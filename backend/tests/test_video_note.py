"""AI video notes for the one allowlisted owner account: allowlist, cache by request hash,
no "recording" status, failures, API, encoder and the Broker client.
No real Broker, ffmpeg or Telegram is used."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import respx
from conftest import OPERATOR, OWNER, FakeConnector, FakePort, decision_json, login
from sqlalchemy import select

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import (
    Channel,
    Client,
    Dialog,
    Job,
    Message,
    OutboundSendIntent,
    PersonaMedia,
)
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.session import transaction
from sim.adapters.llm.broker import BrokerLLM
from sim.adapters.media import MediaStore
from sim.adapters.video import VIDEO_NOTE_SIDE, VideoNoteEncoder, video_note_args
from sim.domain.enums import (
    Author,
    ChannelKind,
    Delivery,
    Direction,
    MediaKind,
    SessionStatus,
)
from sim.ports.channel import SendResult
from sim.ports.video import VideoError, VideoResult
from sim.services import jobkinds
from sim.services.ai_media import is_ai_video
from sim.services.app_settings import BY_KEY, AppSettingsService
from sim.services.instructions import NEUTRAL_MEDIA_SCENE
from sim.services.media_decisions import (
    QUEUED,
    SEND_FAILED,
    SENT,
    MediaDecision,
    MediaDecisionLog,
)
from sim.services.send_receipts import SendUncertain
from sim.services.video_note import (
    AI_VIDEO_PREFIX,
    CHAT_AUDIO_VALIDATION,
    OWNER_SETTING,
    VIDEO_NOTE_WORKFLOW,
    ChatVideoTarget,
    Reference,
    VideoNoteRefused,
    VideoNoteRequest,
    cache_key,
    cache_name,
)
from sim.worker.handlers import HANDLERS
from tests.media_image_fixtures import JPEG

OWNER_TG = 169510539
STRANGER_TG = 555000111
MP4 = b"\x00\x00\x00\x18ftypmp42-clip"
NOTE = b"\x00\x00\x00\x18ftypmp42-square"
PROMPT = "Марина улыбается и машет рукой, говорит: привет!"


@dataclass
class NotePort(FakePort):
    """A userbot-like port: can send video notes; records them."""
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VIDEO_NOTE})
    notes: list[tuple[str, bytes]] = field(default_factory=list)

    async def send_video_note(self, external_thread_id: str, data: bytes) -> SendResult:
        self.notes.append((external_thread_id, data))
        return SendResult(ok=True, external_message_id=f"note-{len(self.notes)}")


@dataclass
class FakeVideo:
    error: Exception | None = None
    calls: list[dict] = field(default_factory=list)

    async def generate_video(self, prompt, **kw):
        self.calls.append({"prompt": prompt, **kw})
        if self.error is not None:
            raise self.error
        return VideoResult(video=MP4, mime_type="video/mp4", duration_s=5.0,
                           model="gemini/gemini-omni-1.2-flash", cost_usd=0.116)


@dataclass
class FakeNoteEncoder:
    seen: list[bytes] = field(default_factory=list)

    require_audio: bool = False

    async def to_video_note(self, video: bytes, *, require_audio: bool = False) -> bytes:
        self.require_audio = require_audio
        self.seen.append(video)
        return NOTE


@dataclass
class Scene:
    port: NotePort
    video: FakeVideo
    encoder: FakeNoteEncoder
    persona_id: int
    owner_dialog: int
    stranger_dialog: int


async def allow(container, telegram_id: int) -> None:
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set(OWNER_SETTING, telegram_id, None)


@pytest.fixture
async def scene(container, world) -> Scene:
    """Marina with a Telegram userbot channel and two private dialogs: the owner account and a
    stranger. The allowlist is NOT set yet."""
    connector = FakeConnector(ChannelKind.TELEGRAM_USER)
    connector.port = NotePort(kind=ChannelKind.TELEGRAM_USER)
    container.registry = {**container.registry, ChannelKind.TELEGRAM_USER: connector}
    container.video = FakeVideo()
    container.video_encoder = FakeNoteEncoder()
    dialogs = []
    async with transaction(container.factory) as s:
        channel = Channel(kind=ChannelKind.TELEGRAM_USER, persona_id=world.persona.id,
                          name="marina-userbot", status=SessionStatus.ACTIVE,
                          session_enc=container.box.encrypt_json({"session": "x"}))
        s.add(channel)
        await s.flush()
        for n, tg_id in enumerate((OWNER_TG, STRANGER_TG)):
            client = Client(persona_id=world.persona.id, ref_code=f"vn{n:06d}")
            s.add(client)
            await s.flush()
            dialog = Dialog(channel_id=channel.id, external_thread_id=str(tg_id),
                            client_id=client.id)
            s.add(dialog)
            await s.flush()
            dialogs.append(dialog.id)
    return Scene(connector.port, container.video, container.video_encoder, world.persona.id,
                 dialogs[0], dialogs[1])


async def test_first_generic_video_note_queues_the_neutral_scene(container, scene, fake_llm):
    await allow(container, OWNER_TG)
    reference = await add_media(container, scene.persona_id, MediaKind.PHOTO, JPEG, "jpg")
    async with transaction(container.factory) as s:
        s.add(Message(dialog_id=scene.owner_dialog, direction=Direction.IN,
                      author=Author.CLIENT, text="Пришли кружочек", external_id="synthetic-first"))
    fake_llm.replies = [decision_json(reply="Хорошо", media={"kind": "video_note", "scene": "кухня"})]
    async with transaction(container.factory) as s:
        outcome = await container.conversation(s).run_reply(scene.owner_dialog)
        assert outcome.status == "sent"
        job = await s.scalar(select(Job).where(Job.kind == jobkinds.PERSONA_MEDIA))
        assert job.payload["scene"] == NEUTRAL_MEDIA_SCENE
        assert job.payload["reference_ids"] == [reference]
        assert job.payload["chat_video_target"]["dialog_id"] == scene.owner_dialog
    assert not scene.video.calls and not scene.port.notes


def request_for(scene: Scene, prompt: str = PROMPT, **kw) -> VideoNoteRequest:
    return VideoNoteRequest(scene.persona_id, prompt, **kw)


async def run_job(container, request: VideoNoteRequest) -> dict:
    async with transaction(container.factory) as s:
        return await HANDLERS[jobkinds.VIDEO_NOTE].run(container, s, request.payload())


async def deliver(container, message_id: int) -> Message:
    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(message_id, final=True)
    async with container.factory() as s:
        return await s.get(Message, message_id)


async def messages(container, dialog_id: int) -> list[Message]:
    async with container.factory() as s:
        return list((await s.scalars(
            select(Message).where(Message.dialog_id == dialog_id).order_by(Message.id))).all())


async def jobs(container, kind: str) -> list[Job]:
    async with container.factory() as s:
        return list((await s.scalars(select(Job).where(Job.kind == kind).order_by(Job.id))).all())


# ---- allowlist ---------------------------------------------------------------------------

def test_the_allowlist_is_an_owner_setting_that_defaults_to_nobody():
    spec = BY_KEY[OWNER_SETTING]
    assert (spec.kind, spec.default, spec.min) == ("int", 0, 0)
    assert spec.validate(OWNER_TG) == OWNER_TG


async def test_without_an_allowlist_nothing_is_queued_generated_or_sent(container, scene):
    async with transaction(container.factory) as s:
        with pytest.raises(VideoNoteRefused, match="Получатель не задан"):
            await container.video_notes(s).request(request_for(scene))
    assert (await run_job(container, request_for(scene)))["outcome"] == "refused"
    assert scene.video.calls == [] and scene.port.notes == [] and scene.port.sent == []
    assert await jobs(container, jobkinds.VIDEO_NOTE) == []
    assert await messages(container, scene.owner_dialog) == []


async def test_an_account_that_has_no_dialog_with_the_persona_is_refused(container, scene):
    await allow(container, 777)
    async with transaction(container.factory) as s:
        with pytest.raises(VideoNoteRefused, match="Нет личного диалога"):
            await container.video_notes(s).request(request_for(scene))
    assert scene.video.calls == []


async def test_only_a_userbot_private_dialog_counts(container, scene, world):
    """The same id as a thread of a bot channel is not the owner account dialog."""
    async with transaction(container.factory) as s:
        client = Client(persona_id=world.persona.id, ref_code="vnbot001")
        s.add(client)
        await s.flush()
        s.add(Dialog(channel_id=world.channel.id, external_thread_id="424242",
                     client_id=client.id))
    await allow(container, 424242)
    async with transaction(container.factory) as s:
        with pytest.raises(VideoNoteRefused, match="Нет личного диалога"):
            await container.video_notes(s).request(request_for(scene))


# ---- the happy path ------------------------------------------------------------------------

async def test_note_goes_to_the_owner_account_only(container, scene):
    await allow(container, OWNER_TG)
    async with transaction(container.factory) as s:
        queued = await container.video_notes(s).request(request_for(scene))
    assert queued["status"] == "queued" and queued["cached"] is False
    assert queued["dialog_id"] == scene.owner_dialog
    (job,) = await jobs(container, jobkinds.VIDEO_NOTE)
    assert job.max_attempts == 1               # a clip is billed: never retried blindly

    done = await run_job(container, VideoNoteRequest.from_payload(job.payload))
    assert done["outcome"] == "queued" and done["cached"] is False
    assert scene.video.calls == [{
        "prompt": PROMPT, "duration_s": 5, "resolution": "360p", "aspect_ratio": "9:16",
        "image": None, "image_mime": None, "workflow": VIDEO_NOTE_WORKFLOW}]
    assert scene.encoder.seen == [MP4]

    (note,) = await messages(container, scene.owner_dialog)
    assert note.media_kind == MediaKind.VIDEO_NOTE and is_ai_video(note.media_url)
    assert note.cost_usd == pytest.approx(0.116) and note.model == "gemini/gemini-omni-1.2-flash"
    assert container.media.path(note.media_url.removeprefix("/api/media/")).read_bytes() == NOTE
    (send,) = await jobs(container, jobkinds.SEND)
    assert send.payload == {"message_id": note.id, "final": True}

    assert (await deliver(container, note.id)).delivery is Delivery.SENT
    assert scene.port.sent == []
    assert scene.port.notes == [(str(OWNER_TG), NOTE)]
    assert scene.port.actions == []            # no "recording a video note" status is shown
    assert await messages(container, scene.stranger_dialog) == []


async def test_a_paused_ai_dialog_still_gets_the_requested_note(container, scene):
    await allow(container, OWNER_TG)
    async with transaction(container.factory) as s:
        (await s.get(Dialog, scene.owner_dialog)).ai_paused = True
    await run_job(container, request_for(scene))
    (note,) = await messages(container, scene.owner_dialog)
    assert (await deliver(container, note.id)).delivery is Delivery.SENT


# ---- cache ---------------------------------------------------------------------------------

async def test_the_same_request_reuses_the_file_and_a_new_one_is_generated(container, scene):
    await allow(container, OWNER_TG)
    first = await run_job(container, request_for(scene))
    again = await run_job(container, request_for(scene, "  Марина улыбается и   машет рукой, "
                                                        "говорит: привет! "))
    assert (first["cached"], again["cached"]) == (False, True)
    assert len(scene.video.calls) == 1 and len(scene.encoder.seen) == 1
    async with transaction(container.factory) as s:
        assert (await container.video_notes(s).request(request_for(scene)))["cached"] is True

    other = await run_job(container, request_for(scene, "Марина пьёт кофе"))
    assert other["cached"] is False and len(scene.video.calls) == 2
    notes = [m for m in await messages(container, scene.owner_dialog) if m.media_kind]
    assert notes[0].media_url == notes[1].media_url != notes[2].media_url
    assert notes[1].cost_usd is None            # a cache hit costs nothing


async def test_one_pending_job_per_request(container, scene):
    await allow(container, OWNER_TG)
    async with transaction(container.factory) as s:
        await container.video_notes(s).request(request_for(scene))
        await container.video_notes(s).request(request_for(scene))
        await container.video_notes(s).request(request_for(scene, "другой запрос"))
    assert len(await jobs(container, jobkinds.VIDEO_NOTE)) == 2


def test_cache_key_covers_persona_prompt_parameters_and_reference():
    base = VideoNoteRequest(1, "привет  мир")
    key = cache_key(base, None)
    assert key == cache_key(VideoNoteRequest(1, " привет мир "), None)
    different = [
        cache_key(VideoNoteRequest(2, "привет мир"), None),
        cache_key(VideoNoteRequest(1, "привет, мир"), None),
        cache_key(VideoNoteRequest(1, "привет мир", duration_s=6), None),
        cache_key(VideoNoteRequest(1, "привет мир", resolution="720p"), None),
        cache_key(base, Reference(b"photo-a", "image/jpeg")),
        cache_key(base, Reference(b"photo-b", "image/jpeg")),
    ]
    assert len({key, *different}) == 7
    name = cache_name(key)
    assert name.startswith(AI_VIDEO_PREFIX) and name.endswith(".mp4")
    assert is_ai_video(f"/api/media/{name}") and not is_ai_video("/api/media/abcdefgh1234.mp4")
    assert not is_ai_video(None)


# ---- the send gate -------------------------------------------------------------------------

async def operator_note(container, dialog_id: int, media_url: str) -> int:
    """A queued operator message that carries a video note file."""
    async with transaction(container.factory) as s:
        message = Message(dialog_id=dialog_id, direction=Direction.OUT, author=Author.OPERATOR,
                          text="", media_kind=MediaKind.VIDEO_NOTE.value, media_url=media_url,
                          delivery=Delivery.QUEUED)
        s.add(message)
        await s.flush()
        return message.id


async def test_an_ai_note_queued_for_anyone_else_is_cancelled_at_send_time(container, scene):
    """Whoever creates the message (an operator, a bug): the SEND job refuses it."""
    await allow(container, OWNER_TG)
    await run_job(container, request_for(scene))
    (note,) = await messages(container, scene.owner_dialog)
    leaked = await operator_note(container, scene.stranger_dialog, note.media_url)
    cancelled = await deliver(container, leaked)
    assert cancelled.delivery is Delivery.CANCELLED and "allowlisted" in cancelled.error
    assert scene.port.notes == [] and scene.port.sent == []


async def test_clearing_the_allowlist_stops_an_already_queued_note(container, scene):
    await allow(container, OWNER_TG)
    await run_job(container, request_for(scene))
    (note,) = await messages(container, scene.owner_dialog)
    await allow(container, 0)
    assert (await deliver(container, note.id)).delivery is Delivery.CANCELLED
    assert scene.port.notes == []


async def test_a_gallery_video_note_is_not_touched_by_the_gate(container, scene):
    name = await container.media.save(b"uploaded-note", "mp4")
    message = await operator_note(container, scene.stranger_dialog, f"/api/media/{name}")
    assert (await deliver(container, message)).delivery is Delivery.SENT
    assert scene.port.notes == [(str(STRANGER_TG), b"uploaded-note")]


# ---- failures ------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    VideoError('video: HTTP 403: {"detail":"project lacks scope: llm:video"}'),
    VideoError("video job 7 error: invalid job (VideoRejected): prompt refused"),
])
async def test_a_failed_generation_is_reported_in_the_dialog_and_sends_nothing(
        container, scene, error):
    await allow(container, OWNER_TG)
    scene.video.error = error
    done = await run_job(container, request_for(scene))
    assert done["outcome"] == "failed" and str(error) in done["error"]
    (failed,) = await messages(container, scene.owner_dialog)
    assert failed.delivery is Delivery.FAILED and failed.error == str(error)
    assert failed.media_url is None
    assert await jobs(container, jobkinds.SEND) == []
    assert scene.port.sent == [] and scene.port.notes == []


async def test_without_a_video_port_the_job_fails_cleanly(container, scene):
    await allow(container, OWNER_TG)
    container.video = None
    assert (await run_job(container, request_for(scene)))["outcome"] == "failed"
    (failed,) = await messages(container, scene.owner_dialog)
    assert "not configured" in failed.error


# ---- reference photo -----------------------------------------------------------------------

async def add_media(container, persona_id: int, kind: MediaKind, data: bytes, suffix: str) -> int:
    name = await container.media.save(data, suffix)
    async with transaction(container.factory) as s:
        row = PersonaMedia(persona_id=persona_id, kind=kind, storage_name=name)
        s.add(row)
        await s.flush()
        return row.id


async def test_a_gallery_photo_is_sent_as_the_reference(container, scene):
    await allow(container, OWNER_TG)
    photo = await add_media(container, scene.persona_id, MediaKind.PHOTO, b"PNG-bytes", "png")
    await run_job(container, request_for(scene, reference_media_id=photo))
    call = scene.video.calls[0]
    assert (call["image"], call["image_mime"]) == (b"PNG-bytes", "image/png")
    assert (await run_job(container, request_for(scene)))["cached"] is False   # other request


async def test_a_reference_must_be_a_small_photo_of_this_persona(container, scene, world):
    await allow(container, OWNER_TG)
    video = await add_media(container, scene.persona_id, MediaKind.VIDEO, b"clip", "mp4")
    gif = await add_media(container, scene.persona_id, MediaKind.PHOTO, b"GIF89a", "gif")
    huge = await add_media(container, scene.persona_id, MediaKind.PHOTO,
                           b"x" * (4 * 1024 * 1024 + 1), "jpg")
    for media_id in (video, gif, huge, 999_999):
        async with transaction(container.factory) as s:
            with pytest.raises(VideoNoteRefused, match="[Рр]еференс"):
                await container.video_notes(s).request(
                    request_for(scene, reference_media_id=media_id))
    assert scene.video.calls == []


# ---- API -----------------------------------------------------------------------------------

async def test_api_is_owner_only_and_validates(api, container, scene):
    url = f"/api/personas/{scene.persona_id}/video-note"
    assert (await api.post(url, json={"prompt": PROMPT})).status_code == 401
    token = await login(api, OPERATOR[0])
    resp = await api.post(url, headers={"X-CSRF-Token": token}, json={"prompt": PROMPT})
    assert resp.status_code == 403

    token = await login(api, OWNER[0])
    headers = {"X-CSRF-Token": token}
    for body in ({"prompt": "   "}, {"prompt": PROMPT, "duration_s": 2},
                 {"prompt": PROMPT, "duration_s": 16}, {"prompt": PROMPT, "resolution": "4k"}):
        assert (await api.post(url, headers=headers, json=body)).status_code == 422

    refused = await api.post(url, headers=headers, json={"prompt": PROMPT})
    assert refused.status_code == 409 and "Получатель не задан" in refused.json()["detail"]
    assert await jobs(container, jobkinds.VIDEO_NOTE) == []


async def test_api_queues_the_note_for_the_allowlisted_account(api, container, scene):
    token = await login(api, OWNER[0])
    headers = {"X-CSRF-Token": token}
    saved = await api.put(f"/api/settings/{OWNER_SETTING}", headers=headers,
                          json={"value": OWNER_TG})
    assert saved.status_code == 200 and saved.json()["value"] == OWNER_TG

    resp = await api.post(f"/api/personas/{scene.persona_id}/video-note", headers=headers,
                          json={"prompt": PROMPT, "duration_s": 6, "resolution": "720p"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued" and body["cached"] is False
    assert body["dialog_id"] == scene.owner_dialog and len(body["cache_key"]) == 64
    (job,) = await jobs(container, jobkinds.VIDEO_NOTE)
    assert job.payload == {"persona_id": scene.persona_id, "prompt": PROMPT, "duration_s": 6,
                           "resolution": "720p", "reference_media_id": None}
    missing = await api.post("/api/personas/999999/video-note", headers=headers,
                             json={"prompt": PROMPT})
    assert missing.status_code == 409


@pytest.mark.parametrize("before_send", ["allowed", "paused", "missing", "revoked", "archived", "late_pause", "claim_pause", "claim_revoke", "legacy_audio"])
async def test_chat_video_stays_on_origin_channel(container, scene, before_send, monkeypatch):
    connector = container.registry[ChannelKind.TELEGRAM_USER]
    original_build = connector.build_port
    port_channels = []

    def track_port(channel_id, credentials):
        port_channels.append(channel_id)
        return original_build(channel_id, credentials)

    monkeypatch.setattr(connector, "build_port", track_port)
    await allow(container, OWNER_TG)
    reference = await add_media(container, scene.persona_id, MediaKind.PHOTO, JPEG, "jpg")
    async with transaction(container.factory) as s:
        original = await s.get(Dialog, scene.owner_dialog)
        channel = Channel(kind=ChannelKind.TELEGRAM_USER, persona_id=scene.persona_id,
                          name="second-userbot", status=SessionStatus.ACTIVE,
                          session_enc=container.box.encrypt_json({"session": "second"}))
        s.add(channel)
        await s.flush()
        dialog = Dialog(channel_id=channel.id, external_thread_id=original.external_thread_id,
                        client_id=original.client_id)
        s.add(dialog)
        await s.flush()
        target = ChatVideoTarget.from_dialog(dialog)
        decisions = MediaDecisionLog(s)
        row = await decisions.add(dialog, MediaDecision(
            "video_note", "video_note", "send", QUEUED, scene="Synthetic",
            planned_at_utc="2026-11-02T05:00:00+00:00", planned_light="утро",
            reference_ids=(reference,),
        ))
        result = await HANDLERS[jobkinds.PERSONA_MEDIA].run(container, s, {
            "dialog_id": dialog.id, "kind": "video_note", "scene": "Synthetic",
            "chat_video_target": target.payload(), "decision_id": row.id,
            "reference_ids": [reference],
            "shot_environment": {"at": "2026-11-02T05:00:00+00:00", "light": "утро"},
        })
        message = await s.get(Message, result["message_id"])
        assert message.dialog_id == dialog.id != original.id
        job = await s.scalar(select(Job).where(Job.dedup_key == jobkinds.send_key(message.id)))
        assert job.payload["final"] is False
        assert job.payload["chat_video_target"] == target.payload()
        payload = dict(job.payload)
        assert payload["chat_audio_validation"] == CHAT_AUDIO_VALIDATION
        if before_send == "legacy_audio":
            payload.pop("chat_audio_validation")
        name = message.media_url.removeprefix("/api/media/")
        assert scene.encoder.require_audio
        assert scene.video.calls[0]["image"] == JPEG
        assert scene.video.calls[0]["image_mime"] == "image/jpeg"

    if before_send == "missing":
        container.media.path(name).unlink()
    if before_send == "revoked":
        await allow(container, 0)
    async with transaction(container.factory) as s:
        if before_send == "paused":
            dialog = await s.get(Dialog, target.dialog_id)
            dialog.ai_paused = True
            await s.flush()
        if before_send == "archived":
            channel = await s.get(Channel, target.channel_id)
            channel.is_archived = True
            await s.flush()
        if before_send == "late_pause":
            outbound = container.outbound(s)

            async def pause_after_first_gate(_message_id, _fingerprint):
                current = await s.get(Dialog, target.dialog_id)
                current.ai_paused = True
                await s.flush()
                return None

            outbound.receipts.previous = pause_after_first_gate
            await outbound.deliver(payload["message_id"], chat_video_target=target,
                                   media_decision_id=payload["media_decision_id"],
                                   chat_audio_validation=payload["chat_audio_validation"])
        elif before_send in {"claim_pause", "claim_revoke"}:
            outbound = container.outbound(s)
            original_claim = outbound.receipts.claim

            async def change_after_claim(message_id, fingerprint):
                await original_claim(message_id, fingerprint)
                if before_send == "claim_revoke":
                    await allow(container, 0)
                else:
                    async with transaction(container.factory) as other:
                        current = await other.get(Dialog, target.dialog_id)
                        current.ai_paused = True

            monkeypatch.setattr(outbound.receipts, "claim", change_after_claim)
            await outbound.deliver(payload["message_id"], chat_video_target=target,
                                   media_decision_id=payload["media_decision_id"],
                                   chat_audio_validation=payload["chat_audio_validation"])
        else:
            await HANDLERS[jobkinds.SEND].run(container, s, payload)
        message = await s.get(Message, payload["message_id"])
        expected = {"allowed": Delivery.SENT, "paused": Delivery.CANCELLED,
                    "revoked": Delivery.CANCELLED, "missing": Delivery.FAILED,
                    "archived": Delivery.CANCELLED, "late_pause": Delivery.CANCELLED,
                    "claim_pause": Delivery.CANCELLED, "claim_revoke": Delivery.CANCELLED,
                    "legacy_audio": Delivery.CANCELLED}
        assert message.delivery is expected[before_send]
        if before_send != "allowed":
            ((record, decision),) = await MediaDecisionLog(s).recent(target.dialog_id, 10)
            assert record.id == payload["media_decision_id"]
            assert decision.media_message_id == message.id
            assert decision.outcome == SEND_FAILED and decision.detail in message.error
            if before_send == "legacy_audio":
                assert "lacks current PCM validation" in decision.detail
                assert not port_channels
            intent = await s.get(OutboundSendIntent, message.id)
            if before_send in {"claim_pause", "claim_revoke"}:
                assert intent is not None and intent.sent_at is None and intent.external_id is None
                assert "aborted before transport" in decision.detail
            else:
                assert intent is None
    if before_send == "allowed":
        assert port_channels == [target.channel_id]
    if before_send in {"claim_pause", "claim_revoke"}:
        await allow(container, OWNER_TG)
        async with transaction(container.factory) as s:
            dialog = await s.get(Dialog, target.dialog_id)
            dialog.ai_paused = False
            await s.flush()
            await HANDLERS[jobkinds.SEND].run(container, s, payload)
            message = await s.get(Message, payload["message_id"])
            assert message.delivery is Delivery.CANCELLED
            assert "aborted before transport" in message.error
    assert scene.port.notes == ([(target.recipient, NOTE)] if before_send == "allowed" else [])
    assert not scene.port.sent


@pytest.mark.parametrize("original_text", [None, "", "Synthetic scene"])
async def test_legacy_chat_video_without_binding_is_cancelled_but_manual_still_sends(
    container, scene, original_text,
):
    await allow(container, OWNER_TG)
    result = await run_job(container, request_for(scene))
    async with transaction(container.factory) as s:
        message = await s.get(Message, result["message_id"])
        assert message.original_text is None
        message.original_text = original_text
        dialog = await s.get(Dialog, message.dialog_id)
        dialog.ai_paused = True
        await s.flush()
        job = await s.scalar(select(Job).where(Job.dedup_key == jobkinds.send_key(message.id)))
        assert job.payload == {"message_id": message.id, "final": True}
        payload = dict(job.payload)
    # The worker sees committed state. Leaving the setup writer open blocks the
    # independent receipt transaction on SQLite.
    async with transaction(container.factory) as s:
        await HANDLERS[jobkinds.SEND].run(container, s, payload)
        message = await s.get(Message, payload["message_id"])
        assert message.delivery is (Delivery.SENT if original_text is None else Delivery.CANCELLED)
        if original_text is not None:
            assert await s.get(OutboundSendIntent, message.id) is None
    assert len(scene.port.notes) == int(original_text is None)
    assert not scene.port.sent


@pytest.mark.parametrize("intent_state", ["acked", "unknown", "mismatch", "absent"])
@pytest.mark.parametrize("route", ["unbound", "bound", "changed", "missing"])
async def test_legacy_chat_video_reconciles_receipt_without_transport(
    container, scene, monkeypatch, intent_state, route,
):
    await allow(container, OWNER_TG)
    result = await run_job(container, request_for(scene))
    message_id = result["message_id"]
    async with transaction(container.factory) as s:
        message = await s.get(Message, message_id)
        message.original_text = "Synthetic scene"
        dialog = await s.get(Dialog, message.dialog_id)
        dialog.ai_paused = True
        binding = ChatVideoTarget.from_dialog(dialog).payload()
        channel = await s.get(Channel, dialog.channel_id)
        fingerprint = hashlib.sha256(json.dumps({
            "dialog_id": dialog.id, "channel_id": channel.id, "kind": channel.kind.value,
            "thread": dialog.external_thread_id, "text": message.text,
            "media_url": message.media_url, "voice": False,
            "session_sha256": hashlib.sha256((channel.session_enc or "").encode()).hexdigest(),
        }, sort_keys=True).encode()).hexdigest()
        journal = MediaDecisionLog(s)
        record = await journal.add(dialog, MediaDecision(
            requested=MediaKind.VIDEO_NOTE.value, kind=MediaKind.VIDEO_NOTE.value,
            decision="send", outcome=QUEUED, scene="Synthetic scene", detail="Synthetic",
        ))
        await journal.generated(record.id, {"outcome": "queued", "message_id": message_id})
        record_id = record.id
        receipts = container.outbound(s).receipts

    if intent_state == "mismatch":
        fingerprint = "0" * 64
    if intent_state != "absent":
        await receipts.claim(message_id, fingerprint)
        if intent_state != "unknown":
            await receipts.acknowledge(message_id, fingerprint, "synthetic-ack")
    await allow(container, 0)

    def no_transport(*_args, **_kwargs):
        pytest.fail("legacy receipt recovery must not build a transport")

    monkeypatch.setattr(FakeConnector, "build_port", no_transport)
    if route == "missing":
        async def no_dialog(self, _dialog_id):
            return None

        monkeypatch.setattr(DialogRepo, "get", no_dialog)
    if route == "changed":
        binding["channel_id"] += 1
    recoverable = intent_state == "acked" and route in {"unbound", "bound"}
    async with transaction(container.factory) as s:
        payload = {"message_id": message_id, "final": route == "unbound"}
        if route != "unbound":
            payload.update(chat_video_target=binding, media_decision_id=record_id)
        if recoverable or intent_state == "absent":
            await HANDLERS[jobkinds.SEND].run(container, s, payload)
        else:
            with pytest.raises(SendUncertain):
                await HANDLERS[jobkinds.SEND].run(container, s, payload)
        message = await s.get(Message, message_id)
        ((row, decision),) = await MediaDecisionLog(s).recent(message.dialog_id, 10)
        assert row.id == record_id
        if recoverable:
            assert message.delivery is Delivery.SENT and message.external_id == "synthetic-ack"
            assert decision.outcome == SENT
        elif intent_state == "absent":
            assert message.delivery is Delivery.CANCELLED and decision.outcome == SEND_FAILED
            assert await s.get(OutboundSendIntent, message_id) is None
        else:
            assert message.delivery is Delivery.QUEUED and message.external_id is None
    assert not scene.port.notes and not scene.port.sent


@pytest.mark.parametrize("multiple", [False, True])
async def test_chat_video_requires_exactly_one_planned_persona_reference(container, scene, multiple):
    await allow(container, OWNER_TG)
    reference = await add_media(container, scene.persona_id, MediaKind.PHOTO, JPEG, "jpg")
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, scene.owner_dialog)
        result = await HANDLERS[jobkinds.PERSONA_MEDIA].run(container, s, {
            "dialog_id": dialog.id, "kind": "video_note", "scene": "Synthetic",
            "chat_video_target": ChatVideoTarget.from_dialog(dialog).payload(),
            "reference_ids": [reference, reference + 1] if multiple else [],
            "shot_environment": {"at": "2026-11-02T05:00:00+00:00", "light": "утро"},
        })
        assert result["outcome"] == "refused"
        assert not scene.video.calls


async def test_chat_video_checks_pause_again_after_generation(container, scene):
    await allow(container, OWNER_TG)
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, scene.owner_dialog)

        class PausingVideo(FakeVideo):
            async def generate_video(self, prompt, **kw):
                dialog.ai_paused = True
                await s.flush()
                return await super().generate_video(prompt, **kw)

        notes = container.video_notes(s)
        notes.video = PausingVideo()
        result = await notes.run(
            request_for(scene).payload(), target=ChatVideoTarget.from_dialog(dialog),
            chat_reference=Reference(JPEG, "image/jpeg"),
        )
        assert result["outcome"] == "refused"
        assert await s.scalar(select(Job.id).where(Job.kind == jobkinds.SEND)) is None


# ---- encoder -------------------------------------------------------------------------------

ENCODER_MP4 = (b"\x00\x00\x00\x10ftypmp42\x00\x00\x00\x00"
               b"\x00\x00\x00\x09moovx\x00\x00\x00\x09mdatx")
PROBE = json.dumps({"streams": [
    {"codec_type": "video", "codec_name": "h264", "width": 384, "height": 384,
     "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac"}],
    "format": {"duration": "1"}}).encode()


async def test_encoder_crops_to_a_square_and_keeps_the_audio_track():
    seen = {}

    async def runner(args, data, limit_s):
        if args[0] == "ffprobe":
            return 0, PROBE, b""
        seen.update(args=args, data=data, limit_s=limit_s)
        source = Path(args[args.index("-i") + 1])
        assert await asyncio.to_thread(source.read_bytes) == ENCODER_MP4
        return 0, ENCODER_MP4, b""

    async def audio_runner(args, data, limit_s):
        assert args[-3:] == ("-f", "s16le", "pipe:1")
        assert args[args.index("-i") + 1].endswith("/out.mp4")
        assert data == b"" and limit_s == 7
        return 0, b"\x10\x00" * 320, b""

    encoder = VideoNoteEncoder(runner, timeout_s=7, audio_runner=audio_runner)
    assert await encoder.to_video_note(ENCODER_MP4, require_audio=True) == ENCODER_MP4
    args = seen["args"]
    assert seen["data"] == b"" and seen["limit_s"] == 7
    assert args == video_note_args(args[args.index("-i") + 1], args[-1])
    vf = args[args.index("-vf") + 1]
    assert "crop=" in vf and f"scale={VIDEO_NOTE_SIDE}:{VIDEO_NOTE_SIDE}" in vf
    assert args[args.index("-c:a") + 1] == "aac" and "-an" not in args   # the sound stays
    assert "-af" not in args and "-filter_complex" not in args           # nothing is mixed in
    assert args[args.index("-movflags") + 1] == "+frag_keyframe+empty_moov"
    assert args[-1] == "pipe:1"


async def test_chat_encoder_rejects_a_silent_clip_before_encoding():
    probe = json.loads(PROBE)
    probe["streams"].pop()

    async def runner(args, _data, _limit_s):
        assert args[0] == "ffprobe"
        return 0, json.dumps(probe).encode(), b""

    with pytest.raises(VideoError, match="нет звуковой дорожки"):
        await VideoNoteEncoder(runner).to_video_note(ENCODER_MP4, require_audio=True)


@pytest.mark.parametrize("result", [(1, b"", b"Invalid data"), (0, b"", b"")])
async def test_encoder_failure_is_a_video_error(result):
    async def runner(args, data, limit_s):
        if args[0] == "ffprobe":
            return 0, PROBE, b""
        return result

    with pytest.raises(VideoError, match="безопасно"):
        await VideoNoteEncoder(runner).to_video_note(ENCODER_MP4)


async def test_encoder_rejects_empty_input_and_a_missing_binary():
    from sim.adapters.audio import AudioError

    with pytest.raises(VideoError, match="empty"):
        await VideoNoteEncoder().to_video_note(b"")

    async def no_ffmpeg(args, data, limit_s):
        raise AudioError("ffmpeg unavailable: FileNotFoundError")

    with pytest.raises(VideoError, match="недоступна"):
        await VideoNoteEncoder(no_ffmpeg).to_video_note(ENCODER_MP4)


# ---- media store ---------------------------------------------------------------------------

async def test_media_store_saves_a_cache_entry_under_its_name(tmp_path):
    store = MediaStore(tmp_path / "media")
    name = cache_name("ab" * 32)
    assert store.path(name) is None
    assert await store.save_as(name, NOTE) == name
    assert store.path(name).read_bytes() == NOTE
    await store.save_as(name, b"new")                                  # replaced in one step
    assert store.path(name).read_bytes() == b"new"
    assert [p.name for p in (tmp_path / "media").iterdir()] == [name]
    with pytest.raises(ValueError, match="unsafe"):
        await store.save_as("../escape.mp4", NOTE)


# ---- Broker client (/v1/video/jobs) --------------------------------------------------------

URL = "https://broker.test"


async def no_sleep(_: float) -> None:
    return None


def broker() -> BrokerLLM:
    return BrokerLLM(URL, "key-1", sleep=no_sleep)


@respx.mock
async def test_broker_submits_a_video_job_and_polls_for_the_clip():
    submit = respx.post(f"{URL}/v1/video/jobs").respond(
        202, json={"job_id": 9, "status": "pending", "poll_url": "/v1/jobs/9", "poll_after_s": 10})
    respx.get(f"{URL}/v1/jobs/9").mock(side_effect=[
        httpx.Response(200, json={"job_id": 9, "status": "pending", "poll_after_s": 5}),
        httpx.Response(200, json={
            "job_id": 9, "status": "done", "text": None,
            "video_base64": base64.b64encode(MP4).decode(), "mime_type": "video/mp4",
            "video_s": 5.0, "model": "gemini/gemini-omni-1.2-flash", "cost_usd": 0.116}),
    ])
    res = await broker().generate_video(PROMPT, duration_s=5, resolution="360p",
                                        image=b"jpeg", image_mime="image/jpeg",
                                        workflow="video-note")
    assert res.video == MP4 and res.mime_type == "video/mp4" and res.duration_s == 5.0
    assert res.cost_usd == pytest.approx(0.116) and res.model == "gemini/gemini-omni-1.2-flash"
    request = submit.calls.last.request
    assert request.headers["X-Project-Key"] == "key-1"
    assert json.loads(request.content) == {
        "prompt": PROMPT, "duration_s": 5, "resolution": "360p", "aspect_ratio": "9:16",
        "image_base64": base64.b64encode(b"jpeg").decode(), "image_mime": "image/jpeg",
        "workflow": "video-note"}


@respx.mock
async def test_broker_without_the_scope_says_so():
    respx.post(f"{URL}/v1/video/jobs").respond(
        403, json={"detail": "project lacks scope: llm:video"})
    with pytest.raises(VideoError, match="403.*project lacks scope: llm:video"):
        await broker().generate_video(PROMPT, duration_s=5, resolution="360p")


@pytest.mark.parametrize("done", [
    {"job_id": 9, "status": "error", "error": "invalid job (VideoRejected): refused"},
    {"job_id": 9, "status": "done", "video_base64": ""},
    {"job_id": 9, "status": "done"},
    {"job_id": 9, "status": "done", "video_base64": "%%%"},
])
@respx.mock
async def test_broker_job_failures_are_video_errors(done):
    respx.post(f"{URL}/v1/video/jobs").respond(202, json={"job_id": 9, "poll_after_s": 1})
    respx.get(f"{URL}/v1/jobs/9").respond(200, json=done)
    with pytest.raises(VideoError):
        await broker().generate_video(PROMPT, duration_s=5, resolution="360p")
