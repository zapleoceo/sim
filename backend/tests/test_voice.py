"""Persona voice notes: when a reply is voiced, rendering through the SpeechPort, OGG/Opus
transcoding, delivery through the channel's send_voice and the text fallbacks."""
from __future__ import annotations

import logging
import shutil
import struct
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from sim.adapters.audio import OGG_OPUS_ARGS, AudioError, OggOpusEncoder
from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Dialog, Job, Message, Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import Delivery, VoiceMode
from sim.domain.voice import GEMINI_VOICES, speaks
from sim.ports.speech import SpeechError, SpeechResult
from sim.services.voice import VOICE_NOTE, VOICE_WORKFLOW, VoiceRenderer
from tests.conftest import inbound
from tests.test_ingest import ingest

WAV = b"RIFF" + b"\x00" * 40


@dataclass
class FakeSpeech:
    error: Exception | None = None
    calls: list[tuple[str, str, str, str | None]] = field(default_factory=list)

    async def synthesize(self, text, *, voice, style="", workflow=None):
        self.calls.append((text, voice, style, workflow))
        if self.error is not None:
            raise self.error
        return SpeechResult(audio=WAV, mime_type="audio/wav", duration_s=4.2,
                            model="gemini/gemini-3.8-flash-tts", cost_usd=0.001)


@dataclass
class FakeEncoder:
    error: Exception | None = None
    seen: list[bytes] = field(default_factory=list)
    ambiences: list[str] = field(default_factory=list)

    async def to_voice_note(self, audio: bytes, ambience: str = "off") -> bytes:
        self.ambiences.append(ambience)
        self.seen.append(audio)
        if self.error is not None:
            raise self.error
        return b"OggS-" + audio[:4]


# ---- the rule --------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,voice,last,expected", [
    (VoiceMode.OFF, "Kore", "voice", False),
    (VoiceMode.MIRROR, "Kore", "voice", True),
    (VoiceMode.MIRROR, "Kore", "audio", True),
    (VoiceMode.MIRROR, "Kore", None, False),
    (VoiceMode.MIRROR, "Kore", "image", False),
    (VoiceMode.ALWAYS, "Kore", None, True),
    (VoiceMode.ALWAYS, "", None, False),
    (VoiceMode.ALWAYS, "elevenlabs-id", None, False),
])
def test_speaks(mode, voice, last, expected):
    assert speaks(mode, voice, last) is expected


def test_voice_list_is_the_13_female_voices_checked_for_ru_uk():
    assert len(GEMINI_VOICES) == len(set(GEMINI_VOICES)) == 13
    assert {"Kore", "Aoede", "Sulafat"} <= set(GEMINI_VOICES)
    assert not {"Puck", "Charon", "Laomedeia"} & set(GEMINI_VOICES)
    assert not speaks(VoiceMode.ALWAYS, "Puck", None)


# ---- encoder --------------------------------------------------------------------------------

async def test_encoder_pipes_audio_through_ffmpeg_to_ogg_opus():
    seen = {}

    async def runner(args, data, limit_s):
        seen.update(args=args, data=data, limit_s=limit_s)
        return 0, b"OggS...", b""

    assert await OggOpusEncoder(runner, timeout_s=5).to_voice_note(WAV) == b"OggS..."
    assert seen["args"] == OGG_OPUS_ARGS and seen["data"] == WAV and seen["limit_s"] == 5
    assert "libopus" in OGG_OPUS_ARGS and OGG_OPUS_ARGS[-3:] == ("-f", "ogg", "pipe:1")


@pytest.mark.parametrize("result", [(1, b"", b"Invalid data"), (0, b"", b"")])
async def test_encoder_failure_is_audio_error(result):
    async def runner(args, data, limit_s):
        return result

    with pytest.raises(AudioError, match="ffmpeg exited"):
        await OggOpusEncoder(runner).to_voice_note(WAV)


async def test_encoder_rejects_empty_input_and_missing_binary():
    with pytest.raises(AudioError, match="empty"):
        await OggOpusEncoder().to_voice_note(b"")

    from sim.adapters.audio import run_process

    with pytest.raises(AudioError, match="unavailable"):
        await run_process(("definitely-not-a-binary-sim",), b"x", 1.0)


def wav_silence(seconds: float = 0.5, rate: int = 24000) -> bytes:
    pcm = b"\x00\x00" * int(seconds * rate)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
async def test_real_ffmpeg_makes_an_ogg_opus_note():
    out = await OggOpusEncoder().to_voice_note(wav_silence())
    assert out.startswith(b"OggS") and b"OpusHead" in out[:200]


# ---- renderer -------------------------------------------------------------------------------

async def test_renderer_synthesizes_encodes_and_stores(container):
    speech, encoder = FakeSpeech(), FakeEncoder()
    note = await VoiceRenderer(speech, encoder, container.media).render(
        "Привет!", voice="Aoede", style="тепло")
    assert speech.calls == [("Привет!", "Aoede", "тепло", VOICE_WORKFLOW)]
    assert encoder.seen == [WAV] and note.audio == b"OggS-RIFF"
    assert note.duration_s == 4.2 and note.cost_usd == 0.001
    name = note.media_url.removeprefix("/api/media/")
    assert name.endswith(".ogg") and container.media.path(name).read_bytes() == b"OggS-RIFF"


# ---- reply → queue → deliver ----------------------------------------------------------------

async def voiced_world(container, world, connector, *, mode=VoiceMode.MIRROR, voice="Kore",
                       can_send_voice=True, speech=None, encoder=None):
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.voice_mode, persona.voice_id, persona.voice_style = mode, voice, "спокойно"
    container.speech = speech if speech is not None else FakeSpeech()
    container.encoder = encoder if encoder is not None else FakeEncoder()
    caps = {Capability.DOWNLOAD_MEDIA} | ({Capability.SEND_VOICE} if can_send_voice else set())
    connector.port.capabilities = frozenset(caps)


async def reply_to(container, world, *, client_voice: bool) -> Message:
    await ingest(container, world.channel.id, inbound("Расскажите подробнее"))
    async with transaction(container.factory) as s:
        last = (await s.scalars(select(Message).order_by(Message.id.desc()))).first()
        if client_voice:
            last.media_kind = "voice"     # as if transcribed by the media job
        dialog = (await s.scalars(select(Dialog))).unique().one()
        outcome = await container.conversation(s).run_reply(dialog.id)
        assert outcome.status == "sent"
        return outcome.message


async def deliver(container, message_id) -> Message:
    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(message_id)
    async with container.factory() as s:
        return await s.get(Message, message_id)


async def test_mirror_voices_a_reply_to_a_voice_note(container, world, connector):
    speech = FakeSpeech()
    await voiced_world(container, world, connector, speech=speech)
    queued = await reply_to(container, world, client_voice=True)
    assert queued.media_kind == VOICE_NOTE and queued.media_url is None
    assert speech.calls == []              # nothing is rendered before the send job runs

    sent = await deliver(container, queued.id)
    assert sent.delivery is Delivery.SENT and sent.external_id == "voice-1"
    assert sent.media_kind == VOICE_NOTE and sent.media_url.startswith("/api/media/")
    assert sent.text == queued.text        # the transcript stays in the dialog
    assert sent.cost_usd == pytest.approx(0.002 + 0.001)   # reply LLM + speech
    assert connector.port.voices == [("chat-1", b"OggS-RIFF", 4.2)]
    assert connector.port.sent == []
    assert speech.calls == [(queued.text, "Kore", "спокойно", VOICE_WORKFLOW)]


async def test_mirror_answers_text_with_text(container, world, connector):
    await voiced_world(container, world, connector)
    queued = await reply_to(container, world, client_voice=False)
    assert queued.media_kind is None


async def test_always_voices_even_a_text_message(container, world, connector):
    await voiced_world(container, world, connector, mode=VoiceMode.ALWAYS)
    queued = await reply_to(container, world, client_voice=False)
    assert queued.media_kind == VOICE_NOTE


async def test_off_never_voices(container, world, connector):
    await voiced_world(container, world, connector, mode=VoiceMode.OFF)
    queued = await reply_to(container, world, client_voice=True)
    assert queued.media_kind is None


@pytest.mark.parametrize("case", ["no_capability", "speech_error", "audio_error",
                                  "voice_removed", "no_speech_port"])
async def test_voice_failures_fall_back_to_text(container, world, connector, caplog, monkeypatch,
                                               case):
    # alembic fileConfig (migration tests) disables loggers created before it ran
    monkeypatch.setattr(logging.getLogger("sim.services.outbound"), "disabled", False)
    speech = FakeSpeech(error=SpeechError("speech: HTTP 503") if case == "speech_error" else None)
    encoder = FakeEncoder(error=AudioError("ffmpeg exited 1") if case == "audio_error" else None)
    await voiced_world(container, world, connector, speech=speech, encoder=encoder,
                       can_send_voice=case != "no_capability")
    queued = await reply_to(container, world, client_voice=True)
    if case == "voice_removed":
        async with transaction(container.factory) as s:
            (await s.get(Persona, world.persona.id)).voice_id = ""
    if case == "no_speech_port":
        container.speech = None

    sent = await deliver(container, queued.id)
    assert sent.delivery is Delivery.SENT and sent.media_kind is None and sent.media_url is None
    assert connector.port.sent == [("chat-1", queued.text)] and connector.port.voices == []
    assert "goes out as text, not voice" in caplog.text


async def test_voice_send_job_is_queued_like_text(container, world, connector):
    await voiced_world(container, world, connector, mode=VoiceMode.ALWAYS)
    queued = await reply_to(container, world, client_voice=False)
    async with container.factory() as s:
        jobs = (await s.scalars(select(Job).where(Job.kind == "send"))).all()
    assert [j.payload["message_id"] for j in jobs] == [queued.id]


# ---- persona API ----------------------------------------------------------------------------

async def test_persona_api_round_trips_voice_settings_and_validates(api):
    from tests.test_api_smoke import owner

    h = await owner(api)
    created = (await api.post("/api/personas", json={"name": "Марина"}, headers=h)).json()
    assert (created["voice_id"], created["voice_mode"], created["voice_style"]) == ("", "off", "")
    body = {"name": "Марина", "voice_id": "Aoede", "voice_mode": "mirror",
            "voice_style": "тепло, спокойно"}
    upd = (await api.put(f"/api/personas/{created['id']}", json=body, headers=h)).json()
    assert (upd["voice_id"], upd["voice_mode"], upd["voice_style"]) == \
        ("Aoede", "mirror", "тепло, спокойно")
    for bad in ({"voice_id": "21m00Tcm4TlvDq8ikWAM"},          # an ElevenLabs id
                {"voice_mode": "always"},                      # voice replies without a voice
                {"voice_mode": "sometimes", "voice_id": "Kore"},
                {"voice_style": "x" * 301, "voice_id": "Kore"}):
        resp = await api.put(f"/api/personas/{created['id']}", json={"name": "Марина", **bad},
                             headers=h)
        assert resp.status_code == 422, bad
