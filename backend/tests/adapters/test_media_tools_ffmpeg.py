"""media_tools against the real ffmpeg / ffprobe on tiny generated inputs (CI installs ffmpeg;
without it these tests are skipped)."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from sim.adapters.media_tools import FfmpegMediaTools, run_process
from sim.domain.life import Ambience
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


@pytest.mark.parametrize("ambience", [a.value for a in Ambience])
async def test_every_scene_background_records_a_voice_note(ambience: str) -> None:
    ogg, duration = await FfmpegMediaTools().voice_note(wav_bytes(0.5), ambience, 4242)
    assert ogg.startswith(b"OggS") and b"OpusHead" in ogg[:200]
    assert duration == pytest.approx(0.5)


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


async def mp4_clip(width: int, height: int, seconds: float, *, sound: bool) -> bytes:
    """A tiny H.264 test pattern (with a sine tone when `sound`), made by ffmpeg itself."""
    inputs = ["-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=10:duration={seconds}"]
    if sound:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    with tempfile.TemporaryDirectory(prefix="sim-test-") as tmp:
        target = Path(tmp) / "clip.mp4"
        result = await run_process(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if sound else []),
             "-shortest", str(target)], 60.0)
        assert result.code == 0, result.stderr
        return await asyncio.to_thread(target.read_bytes)


@pytest.mark.parametrize("sound", [True, False])
async def test_video_becomes_a_round_video_note(sound: bool) -> None:
    media = FfmpegMediaTools()
    note, duration = await media.to_video_note(await mp4_clip(320, 240, 1.0, sound=sound))
    assert note[4:8] == b"ftyp"
    assert note.find(b"moov") < note.find(b"mdat")          # faststart: playable at once
    probe = await media.probe(note, "video/mp4")
    assert (probe.mime, probe.width, probe.height) == ("video/mp4", 640, 640)
    assert duration == pytest.approx(1.0, abs=0.25)
