"""Local PCM and fake-runner coverage; no Telegram, Broker or paid generation."""
from __future__ import annotations

import asyncio
import shutil
from array import array

import pytest

from sim.adapters.audio import AudioError
from sim.adapters.call_ambience import (
    BED_PEAK,
    BED_SECONDS,
    PREPARE_TIMEOUT_S,
    SPEECH_GAIN,
    CallAmbience,
    bed_args,
    prepare_call_ambience,
)
from sim.adapters.pcm import _bytes, _samples, frame_bytes

RATE = 16000


def noise(seconds: int = BED_SECONDS) -> bytes:
    return _bytes(array("h", [1000, -1000]) * (RATE * seconds // 2))


def frame(value: int) -> bytes:
    return _bytes(array("h", [value]) * (RATE // 100))


def test_mixer_bounds_noise_and_loops_complete_little_endian_frames():
    mixer = CallAmbience(noise(1), RATE)
    frames = [mixer.mix(None) for _ in range(130)]
    assert all(len(f) == frame_bytes(RATE) for f in frames)
    assert any(any(f) for f in frames)
    assert max(abs(x) for f in frames for x in _samples(f)) <= BED_PEAK
    assert mixer.position == RATE * 30 // 100
    # Loop endpoints are zero and faded, not a jump between unrelated samples.
    assert mixer.bed[0] == mixer.bed[-1] == 0
    assert abs(mixer.bed[1]) < abs(mixer.bed[RATE // 50])


def test_mixer_ducks_bed_without_rescaling_or_filtering_voice():
    mixer = CallAmbience(noise(), RATE)
    for _ in range(40):
        mixer.mix(None)
    assert mixer.gain == 1.0
    mixer.mix(frame(1000))  # ramp down over one frame
    assert mixer.gain == SPEECH_GAIN
    voice = _bytes(array("h", [1000, -1000, 2000, -2000]) * (RATE // 400))
    mixed = _samples(mixer.mix(voice))
    original = _samples(voice)
    assert all(abs(a - b) <= BED_PEAK * SPEECH_GAIN for a, b in zip(mixed, original, strict=True))
    # Releasing the duck is gradual, not a click or a jump on each model turn.
    mixer.mix(None)
    assert SPEECH_GAIN < mixer.gain < 1.0


def test_mixer_never_overflows_at_voice_peaks_or_reverses_their_sign():
    mixer = CallAmbience(noise(), RATE)
    mixer.gain = SPEECH_GAIN
    mixed = _samples(mixer.mix(_bytes(array("h", [32767, -32768]) * (RATE // 200))))
    assert all(32767 - BED_PEAK <= x <= 32767 for x in mixed[::2])
    assert all(-32768 <= x <= -32768 + BED_PEAK for x in mixed[1::2])


def test_mixer_rejects_torn_frames_and_silent_or_invalid_beds():
    mixer = CallAmbience(noise(), RATE)
    for invalid in (b"", b"\x01", frame(0)[:-2], frame(0) + b"\x00\x00"):
        with pytest.raises(ValueError, match="complete"):
            mixer.mix(invalid)
    for invalid in (b"", b"\x01", frame(0), frame(100)):
        with pytest.raises(ValueError):
            CallAmbience(invalid, RATE)
    with pytest.raises(ValueError, match="sample rate"):
        CallAmbience(noise(), 12345)


@pytest.mark.parametrize("preset", ["off", "random", "contextual", "unknown", ""])
async def test_disabled_unresolved_and_unknown_presets_never_spawn_process(preset):
    async def forbidden(*args):
        pytest.fail("off/unresolved must not launch ffmpeg")

    assert await prepare_call_ambience(preset, RATE, runner=forbidden) is None
    assert bed_args(preset, RATE) is None


async def test_unsupported_sample_rate_stays_clean_without_process():
    async def forbidden(*args):
        pytest.fail("bad sample rate must not launch ffmpeg")

    assert await prepare_call_ambience("room", 12345, runner=forbidden) is None


@pytest.mark.parametrize("preset", ["room", "street", "cafe"])
async def test_factory_uses_finite_local_pcm_and_existing_presets(preset):
    calls = []

    async def runner(args, data, limit_s):
        calls.append((args, data, limit_s))
        return 0, noise(), b""

    mixer = await prepare_call_ambience(preset, RATE, runner=runner)
    assert isinstance(mixer, CallAmbience)
    args, data, limit_s = calls[0]
    assert args[0] == "ffmpeg" and args[-1] == "pipe:1" and data == b""
    assert args[args.index("-t") + 1] == str(BED_SECONDS)
    assert "s16le" in args and "pcm_s16le" in args
    assert limit_s == PREPARE_TIMEOUT_S <= 3.0
    assert ("cafe_chatter.ogg" in " ".join(args)) is (preset == "cafe")


@pytest.mark.parametrize("result", [(1, b"", b"failure"), (0, b"", b""),
                                   (0, b"\x00" * (RATE * BED_SECONDS * 2), b""),
                                   (0, noise() + b"\x01", b"")])
async def test_invalid_render_falls_back_to_clean(result, caplog):
    async def runner(*args):
        return result

    assert await prepare_call_ambience("cafe", RATE, runner=runner) is None
    assert "using clean audio" in caplog.text


@pytest.mark.parametrize("error", [AudioError("missing"), OSError("missing"), TimeoutError()])
async def test_unavailable_or_slow_ffmpeg_does_not_break_call(error):
    async def runner(*args):
        raise error

    assert await prepare_call_ambience("room", RATE, runner=runner) is None


async def test_cancellation_propagates_instead_of_starting_a_clean_call():
    async def runner(*args):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await prepare_call_ambience("room", RATE, runner=runner)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed in CI image")
@pytest.mark.parametrize("preset", ["room", "street", "cafe"])
async def test_real_local_ffmpeg_graph_produces_audible_bounded_bed(preset):
    # Offline asset/render validation only. No model, channel, credentials or network.
    mixer = await prepare_call_ambience(preset, 48000)
    assert mixer is not None
    output = [mixer.mix(None) for _ in range(40)]
    assert all(len(f) == frame_bytes(48000) for f in output)
    assert any(any(f) for f in output)
    assert max(abs(x) for f in output for x in _samples(f)) <= BED_PEAK
