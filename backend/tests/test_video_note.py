"""AI video notes for the one allowlisted owner account: allowlist, cache by request hash, the
"Сгенерировано ИИ" label before the note, no "recording" status, failures, API, encoder and
the Broker client. No real Broker, ffmpeg or Telegram is used."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import respx
from conftest import OPERATOR, OWNER, FakeConnector, FakePort, login
from sqlalchemy import select

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Channel, Client, Dialog, Job, Message, PersonaMedia
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
from sim.services.app_settings import BY_KEY, AppSettingsService
from sim.services.outbound import SendFailed
from sim.services.video_note import (
    AI_LABEL,
    AI_VIDEO_PREFIX,
    OWNER_SETTING,
    VIDEO_NOTE_WORKFLOW,
    Reference,
    VideoNoteRefused,
    VideoNoteRequest,
    cache_key,
    cache_name,
    is_ai_video,
)
from sim.worker.handlers import HANDLERS

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

    async def to_video_note(self, video: bytes) -> bytes:
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

async def test_label_then_note_go_to_the_owner_account_only(container, scene):
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

    label, note = await messages(container, scene.owner_dialog)
    assert label.text == AI_LABEL and label.media_kind is None
    assert note.media_kind == MediaKind.VIDEO_NOTE and is_ai_video(note.media_url)
    assert note.cost_usd == pytest.approx(0.116) and note.model == "gemini/gemini-omni-1.2-flash"
    assert container.media.path(note.media_url.removeprefix("/api/media/")).read_bytes() == NOTE
    sends = await jobs(container, jobkinds.SEND)
    assert [j.payload for j in sends] == [{"message_id": label.id, "final": True},
                                          {"message_id": note.id, "final": True}]
    assert sends[0].run_at < sends[1].run_at

    assert (await deliver(container, label.id)).delivery is Delivery.SENT
    assert (await deliver(container, note.id)).delivery is Delivery.SENT
    assert scene.port.sent == [(str(OWNER_TG), AI_LABEL)]
    assert scene.port.notes == [(str(OWNER_TG), NOTE)]
    assert scene.port.actions == []            # no "recording a video note" status is shown
    assert await messages(container, scene.stranger_dialog) == []


async def test_a_paused_ai_dialog_still_gets_the_requested_note(container, scene):
    await allow(container, OWNER_TG)
    async with transaction(container.factory) as s:
        (await s.get(Dialog, scene.owner_dialog)).ai_paused = True
    await run_job(container, request_for(scene))
    label, note = await messages(container, scene.owner_dialog)
    await deliver(container, label.id)
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


async def test_the_note_waits_until_the_label_is_delivered(container, scene):
    await allow(container, OWNER_TG)
    await run_job(container, request_for(scene))
    label, note = await messages(container, scene.owner_dialog)
    with pytest.raises(SendFailed, match="label"):
        await deliver(container, note.id)
    assert scene.port.notes == []
    await deliver(container, label.id)
    assert (await deliver(container, note.id)).delivery is Delivery.SENT


async def test_an_ai_note_queued_for_anyone_else_is_cancelled_at_send_time(container, scene):
    """Whoever creates the message (an operator, a bug): the SEND job refuses it."""
    await allow(container, OWNER_TG)
    await run_job(container, request_for(scene))
    note = (await messages(container, scene.owner_dialog))[1]
    leaked = await operator_note(container, scene.stranger_dialog, note.media_url)
    cancelled = await deliver(container, leaked)
    assert cancelled.delivery is Delivery.CANCELLED and "allowlisted" in cancelled.error
    assert scene.port.notes == [] and scene.port.sent == []


async def test_clearing_the_allowlist_stops_an_already_queued_note(container, scene):
    await allow(container, OWNER_TG)
    await run_job(container, request_for(scene))
    label, note = await messages(container, scene.owner_dialog)
    await deliver(container, label.id)
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


# ---- encoder -------------------------------------------------------------------------------

def fake_ffmpeg(args) -> None:
    """What ffmpeg does for the encoder: read the input file, write the output file."""
    src, dst = args[args.index("-i") + 1], args[-1]
    assert Path(src).read_bytes() == MP4
    Path(dst).write_bytes(NOTE)


async def test_encoder_crops_to_a_square_and_keeps_the_audio_track():
    seen = {}

    async def runner(args, data, limit_s):
        seen.update(args=args, data=data, limit_s=limit_s)
        fake_ffmpeg(args)
        return 0, b"", b""

    assert await VideoNoteEncoder(runner, timeout_s=7).to_video_note(MP4) == NOTE
    args = seen["args"]
    assert seen["data"] == b"" and seen["limit_s"] == 7
    assert args == video_note_args(args[args.index("-i") + 1], args[-1])
    vf = args[args.index("-vf") + 1]
    assert "crop=" in vf and f"scale={VIDEO_NOTE_SIDE}:{VIDEO_NOTE_SIDE}" in vf
    assert args[args.index("-c:a") + 1] == "aac" and "-an" not in args   # the sound stays
    assert "-af" not in args and "-filter_complex" not in args           # nothing is mixed in
    assert args[args.index("-movflags") + 1] == "+faststart"


@pytest.mark.parametrize("result", [(1, b"", b"Invalid data"), (0, b"", b"")])
async def test_encoder_failure_is_a_video_error(result):
    async def runner(args, data, limit_s):
        return result

    with pytest.raises(VideoError, match="ffmpeg exited"):
        await VideoNoteEncoder(runner).to_video_note(MP4)


async def test_encoder_rejects_empty_input_and_a_missing_binary():
    from sim.adapters.audio import AudioError

    with pytest.raises(VideoError, match="empty"):
        await VideoNoteEncoder().to_video_note(b"")

    async def no_ffmpeg(args, data, limit_s):
        raise AudioError("ffmpeg unavailable: FileNotFoundError")

    with pytest.raises(VideoError, match="unavailable"):
        await VideoNoteEncoder(no_ffmpeg).to_video_note(MP4)


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
