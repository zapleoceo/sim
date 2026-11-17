"""Synthetic-only normalization and fail-closed bounds; executed in CI, never local."""
import asyncio
import json
from pathlib import Path

import pytest
from test_video_note import ENCODER_MP4, PROBE

from sim.adapters.audio import run_process
from sim.adapters.video import VIDEO_NOTE_INPUT_LIMIT, VideoNoteEncoder
from sim.adapters.video_process import ENCODE_LIMIT
from sim.ports.video import VideoError


@pytest.mark.parametrize("data", [b"not mp4", ENCODER_MP4[:-1], b"x" * (VIDEO_NOTE_INPUT_LIMIT + 1)])
async def test_invalid_input_never_starts_decoder(data):
    async def forbidden(*args):
        pytest.fail("invalid input reached decoder")
    with pytest.raises(VideoError):
        await VideoNoteEncoder(forbidden).to_video_note(data)


@pytest.mark.parametrize("duration,width", [(61, 384), (float("nan"), 384), (1, 100000)])
async def test_metadata_limits_reject_before_encoding(duration, width):
    async def runner(args, *_):
        assert args[0] == "ffprobe"
        info = json.loads(PROBE)
        info["format"]["duration"] = duration
        info["streams"][0]["width"] = width
        return 0, json.dumps(info).encode(), b""
    with pytest.raises(VideoError, match="ограничениям"):
        await VideoNoteEncoder(runner).to_video_note(ENCODER_MP4)


@pytest.mark.parametrize("failure", ["large", "corrupt", "lost_audio", "wrong_codec"])
async def test_invalid_output_is_not_returned(failure):
    probes = 0
    async def runner(args, *_):
        nonlocal probes
        if args[0] == "ffprobe":
            probes += 1
            info = json.loads(PROBE)
            if probes == 2 and failure == "lost_audio":
                info["streams"] = info["streams"][:1]
            if probes == 2 and failure == "wrong_codec":
                info["streams"][0]["codec_name"] = "vp9"
            return 0, json.dumps(info).encode(), b""
        result = b"x" * (ENCODE_LIMIT + 1) if failure == "large" else ENCODER_MP4
        return 0, result[:-1] if failure == "corrupt" else result, b""
    with pytest.raises(VideoError):
        await VideoNoteEncoder(runner).to_video_note(ENCODER_MP4)


async def test_encoder_cancellation_cleans_private_files():
    source = None
    async def runner(args, *_):
        nonlocal source
        source = Path(args[-1])
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await VideoNoteEncoder(runner).to_video_note(ENCODER_MP4)
    assert source is not None and not source.parent.exists()


async def test_real_synthetic_portrait_keeps_audio_and_round_format(tmp_path):
    source = tmp_path / "input.mp4"
    code, _, err = await run_process((
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x96:d=0.4", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.4", "-c:v", "libx264", "-c:a", "aac",
        "-shortest", str(source)), b"", 15)
    assert code == 0, err
    note = await VideoNoteEncoder().to_video_note(source.read_bytes())
    target = tmp_path / "output.mp4"
    target.write_bytes(note)
    # probe(output=True) checks codec, square pixels, dimensions, duration and AAC.
    assert await VideoNoteEncoder().probe(target, output=True)
    code, pcm, _ = await run_process(("ffmpeg", "-v", "error", "-i", str(target),
                                     "-map", "0:a:0", "-f", "s16le", "pipe:1"), b"", 8)
    assert code == 0 and len(pcm) > 1000 and any(pcm)
