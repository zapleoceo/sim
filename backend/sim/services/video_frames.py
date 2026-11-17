"""Bounded local video sampling for image-only subscription inputs. Never transcribes audio."""
from __future__ import annotations

import asyncio
import json
import math
import tempfile
from pathlib import Path

import filetype

from sim.adapters.audio import AudioError, Runner, run_process

MAX_VIDEO_BYTES = 5 * 1024 * 1024
MAX_DURATION_S = 60
MAX_FRAMES = 5
MAX_PIXELS = 1920 * 1080
MAX_FRAME_BYTES = 1024 * 1024
MAX_TOTAL_FRAME_BYTES = 1024 * 1024


class VideoUnsupported(ValueError):
    """Invalid/over-limit input, unavailable decoder or unsupported video container."""


async def sample_video(data: bytes, *, runner: Runner = run_process) -> list[bytes]:
    if not data or len(data) > MAX_VIDEO_BYTES:
        raise VideoUnsupported("video_size")
    detected = filetype.guess(data)
    if detected is None or detected.mime not in ("video/mp4", "video/webm"):
        raise VideoUnsupported("video_container")
    # Only a generated path inside a private temporary directory reaches ffmpeg.
    # Local file protocol only: remote references cannot turn parsing into fetches.
    with tempfile.TemporaryDirectory(prefix="sim-video-") as directory:
        source = Path(directory) / "input.bin"
        await asyncio.to_thread(source.write_bytes, data)
        try:
            code, out, _ = await runner([
                "ffprobe", "-v", "error", "-max_alloc", "16777216",
                "-protocol_whitelist", "file",
                "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(source),
            ], b"", 8)
            if code or len(out) > 16384:
                raise VideoUnsupported("video_probe")
            try:
                info = json.loads(out)
                stream = info["streams"][0]
                width, height = int(stream["width"]), int(stream["height"])
                duration = float(info["format"]["duration"])
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                raise VideoUnsupported("video_metadata") from exc
            if (not math.isfinite(duration) or not 0 < duration <= MAX_DURATION_S
                    or width <= 0 or height <= 0 or width * height > MAX_PIXELS):
                raise VideoUnsupported("video_limits")
            frames = []
            for index in range(MAX_FRAMES):
                code, frame, _ = await runner([
                    "ffmpeg", "-nostdin", "-v", "error", "-max_alloc", "16777216",
                    "-protocol_whitelist", "file", "-threads", "1",
                    "-ss", str(duration * index / MAX_FRAMES), "-i", str(source),
                    "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
                    "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                    "-frames:v", "1", "-c:v", "mjpeg", "-threads", "1",
                    "-f", "image2pipe", "pipe:1",
                ], b"", 8)
                signature = filetype.guess(frame)
                if (code or not frame or len(frame) > MAX_FRAME_BYTES or signature is None
                        or signature.mime != "image/jpeg"):
                    raise VideoUnsupported("video_frame")
                frames.append(frame)
                if sum(map(len, frames)) > MAX_TOTAL_FRAME_BYTES:
                    raise VideoUnsupported("video_frames_size")
            return frames
        except AudioError as exc:
            # No raw decoder stderr, paths, input bytes or embedded URLs in diagnostics.
            raise VideoUnsupported("video_decoder_unavailable_or_timeout") from exc
