"""Synthetic-only normalization and fail-closed bounds; executed in CI, never local."""
import asyncio
import json
from pathlib import Path

import pytest
from test_video_note import ENCODER_MP4, PROBE

from sim.adapters.audio import run_process
from sim.adapters.video import (
    CHAT_AUDIO_FRAME_SAMPLES,
    VIDEO_NOTE_INPUT_LIMIT,
    VideoNoteEncoder,
    has_chat_audio_signal,
)
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


@pytest.mark.parametrize("silent", [True, False])
async def test_real_chat_audio_rejects_silent_aac_but_accepts_quiet_signal_with_pause(tmp_path, silent):
    source = tmp_path / "input.mp4"
    audio = "anullsrc=r=48000:cl=mono" if silent else "sine=frequency=440:duration=0.25"
    audio_filter = [] if silent else ["-af", "volume=0.02,adelay=200,apad"]
    code, _, err = await run_process((
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x96:d=0.8", "-f", "lavfi", "-i", audio, *audio_filter,
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source),
    ), b"", 15)
    assert code == 0, err
    encoder = VideoNoteEncoder()
    clip = source.read_bytes()
    if silent:
        with pytest.raises(VideoError, match="не обнаружен звуковой сигнал"):
            await encoder.to_video_note(clip, require_audio=True)
        assert await encoder.to_video_note(clip)  # manual video retains its existing rules
    else:
        assert await encoder.to_video_note(clip, require_audio=True)


def test_chat_pcm_needs_sustained_energy_and_allows_separated_quiet_frames():
    frame = CHAT_AUDIO_FRAME_SAMPLES
    silence = bytes(frame * 2)
    quiet = b"\x08\x00" * frame
    click = b"\xff\x7f" + silence[2:]
    assert not has_chat_audio_signal(silence * 3)
    assert not has_chat_audio_signal(silence + click + silence)
    assert not has_chat_audio_signal(quiet + silence * 3)
    assert has_chat_audio_signal(quiet + silence * 3 + quiet + silence)
