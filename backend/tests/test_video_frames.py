import json

import pytest

from sim.adapters.audio import run_process
from sim.services.video_frames import VideoUnsupported, sample_video

MP4 = b"\0\0\0\x18ftypmp42\0\0\0\0mp42isom"
JPEG = b"\xff\xd8\xff\xdb" + b"synthetic"


async def test_sampling_is_bounded_and_never_enables_network():
    calls = []

    async def runner(args, data, timeout):
        calls.append(args)
        assert data == b"" and timeout == 8
        assert args[args.index("-protocol_whitelist") + 1] == "file"
        if args[0] == "ffprobe":
            return 0, json.dumps({"streams": [{"width": 640, "height": 480}],
                                  "format": {"duration": "60"}}).encode(), b""
        assert args[args.index("-frames:v") + 1] == "1"
        return 0, JPEG, b""

    assert await sample_video(MP4, runner=runner) == [JPEG] * 5
    assert len(calls) == 6


@pytest.mark.parametrize("duration", ["61", "nan", "inf", "0", "-1"])
async def test_invalid_duration_never_decodes_frames(duration):
    async def runner(args, data, timeout):
        assert args[0] == "ffprobe"
        return 0, json.dumps({"streams": [{"width": 640, "height": 480}],
                              "format": {"duration": duration}}).encode(), b""

    with pytest.raises(VideoUnsupported, match="limits"):
        await sample_video(MP4, runner=runner)


async def test_real_offline_decoder_with_synthetic_color_video():
    code, video, _ = await run_process([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=5",
        "-t", "1", "-c:v", "mpeg4", "-movflags", "frag_keyframe+empty_moov",
        "-f", "mp4", "pipe:1",
    ], b"", 10)
    assert code == 0
    frames = await sample_video(video)
    assert len(frames) == 5 and all(frame.startswith(b"\xff\xd8") for frame in frames)
