"""Voice note ambience: "recorded on a phone" in a room, on the street or in a cafe."""
from __future__ import annotations

import random
import shutil

import pytest

from sim.adapters.ambience import CHATTER, PRESETS, ffmpeg_args, resolve
from sim.adapters.audio import OGG_OPUS_ARGS, OggOpusEncoder
from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import VoiceAmbience, VoiceMode
from tests.test_voice import WAV, FakeEncoder, FakeSpeech, deliver, reply_to, voiced_world, wav_silence


def test_off_and_unknown_values_keep_the_clean_encoding():
    rng = random.Random(1)
    assert resolve("off", rng) is VoiceAmbience.OFF
    assert resolve("disco", rng) is VoiceAmbience.OFF
    assert ffmpeg_args(VoiceAmbience.OFF, rng) is None


def test_random_picks_one_of_the_presets():
    rng = random.Random(7)
    picks = {resolve("random", rng) for _ in range(60)}
    assert picks == set(PRESETS)


@pytest.mark.parametrize("ambience", PRESETS)
def test_each_preset_goes_through_the_phone_mic_and_low_bitrate_opus(ambience):
    args = ffmpeg_args(ambience, random.Random(3))
    graph = args[args.index("-filter_complex") + 1]
    assert "highpass=f=160" in graph and "acompressor" in graph and "anoisesrc" in graph
    assert args[-3:] == ("-f", "ogg", "pipe:1") and "24k" in args
    if ambience is VoiceAmbience.CAFE:
        assert str(CHATTER) in args and "-stream_loop" in args


def test_noise_differs_from_note_to_note():
    rng = random.Random(5)
    first = ffmpeg_args(VoiceAmbience.STREET, rng)
    second = ffmpeg_args(VoiceAmbience.STREET, rng)
    assert first != second


def test_the_cafe_chatter_clip_is_bundled():
    assert CHATTER.is_file() and CHATTER.read_bytes().startswith(b"OggS")


async def test_encoder_uses_the_ambience_graph():
    seen = {}

    async def runner(args, data, limit_s):
        seen["args"] = args
        return 0, b"OggS", b""

    encoder = OggOpusEncoder(runner, rng=random.Random(2))
    await encoder.to_voice_note(WAV, "room")
    assert "-filter_complex" in seen["args"]
    await encoder.to_voice_note(WAV)
    assert seen["args"] == OGG_OPUS_ARGS


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
@pytest.mark.parametrize("ambience", ["room", "street", "cafe"])
async def test_real_ffmpeg_renders_every_preset(ambience):
    out = await OggOpusEncoder().to_voice_note(wav_silence(1.0), ambience)
    assert out.startswith(b"OggS") and b"OpusHead" in out[:200]


async def test_a_channel_voice_note_uses_the_persona_ambience(container, world, connector):
    encoder = FakeEncoder()
    await voiced_world(container, world, connector, speech=FakeSpeech(), encoder=encoder)
    async with transaction(container.factory) as s:
        (await s.get(Persona, world.persona.id)).voice_ambience = VoiceAmbience.CAFE
    queued = await reply_to(container, world, client_voice=True)
    await deliver(container, queued.id)
    assert encoder.ambiences == ["cafe"]


async def test_persona_api_round_trips_the_ambience(api):
    from tests.test_api_smoke import owner

    h = await owner(api)
    created = (await api.post("/api/personas", json={"name": "Марина"}, headers=h)).json()
    assert created["voice_ambience"] == "off"
    body = {"name": "Марина", "voice_id": "Kore", "voice_mode": VoiceMode.ALWAYS, "voice_ambience": "street"}
    upd = (await api.put(f"/api/personas/{created['id']}", json=body, headers=h)).json()
    assert upd["voice_ambience"] == "street"
    bad = await api.put(f"/api/personas/{created['id']}", json={**body, "voice_ambience": "disco"}, headers=h)
    assert bad.status_code == 422
