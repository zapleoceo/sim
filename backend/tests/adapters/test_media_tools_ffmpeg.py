"""media_tools against the real ffmpeg / ffprobe on tiny generated inputs (CI installs ffmpeg;
without it these tests are skipped)."""
from __future__ import annotations

import shutil

import pytest

from sim.adapters.media_tools import FfmpegMediaTools
from tests.adapters.helpers import png_bytes, wav_bytes

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg / ffprobe are not installed")


async def test_wav_becomes_an_ogg_opus_voice_note() -> None:
    media = FfmpegMediaTools()
    ogg, duration = await media.wav_to_ogg_opus(wav_bytes(0.5))
    assert ogg.startswith(b"OggS")
    assert b"OpusHead" in ogg[:200]
    assert duration == pytest.approx(0.5)
    probe = await media.probe(ogg, "application/octet-stream")
    assert probe.mime == "audio/ogg"
    assert probe.duration_s == pytest.approx(0.5, abs=0.1)


async def test_audio_to_pcm16k() -> None:
    pcm = await FfmpegMediaTools().to_pcm16k(wav_bytes(0.5, rate=24_000), "audio/wav")
    assert len(pcm) % 2 == 0
    assert len(pcm) == pytest.approx(0.5 * 16_000 * 2, rel=0.05)


async def test_probe_image_dimensions() -> None:
    probe = await FfmpegMediaTools().probe(png_bytes(64, 32), "image/png")
    assert (probe.mime, probe.width, probe.height, probe.duration_s) == (
        "image/png", 64, 32, None)


async def test_large_image_is_shrunk_to_jpeg() -> None:
    media = FfmpegMediaTools()
    data, mime = await media.image_for_model(png_bytes(2400, 1200), "image/png")
    assert mime == "image/jpeg"
    assert data.startswith(b"\xff\xd8")
    probe = await media.probe(data, mime)
    assert (probe.width, probe.height) == (2048, 1024)


async def test_small_png_is_sent_as_is() -> None:
    image = png_bytes(100, 50)
    assert await FfmpegMediaTools().image_for_model(image, "image/png") == (image, "image/png")
