"""Video transcoding for Telegram video notes ("кружочки").

The Broker returns a 9:16 (or 16:9) MP4. Telegram shows a round video note only for a square
clip up to 640 px and one minute, so the clip is centre-cropped to a square and re-encoded once
with ffmpeg (installed in the backend image). The audio track of the clip is kept as it is:
nothing is mixed in and no background noise is added."""
from __future__ import annotations

import tempfile
from pathlib import Path

from sim.adapters.audio import AudioError, Runner, run_process
from sim.ports.video import VideoError

VIDEO_NOTE_SIDE = 384          # px; the size the Telegram apps record themselves
VIDEO_NOTE_MAX_S = 60
FFMPEG_VIDEO_TIMEOUT_S = 120.0


def video_note_args(src: str, dst: str) -> tuple[str, ...]:
    """ffmpeg argv: `src` (any video) → `dst`, a square H.264/AAC MP4 with the index up front."""
    side = VIDEO_NOTE_SIDE
    return (
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
        "-vf", f"crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side},setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1",
        "-t", str(VIDEO_NOTE_MAX_S), "-movflags", "+faststart", dst,
    )


class VideoNoteEncoder:
    """Any clip ffmpeg reads → a square MP4 that Telegram plays as a video note."""

    def __init__(self, runner: Runner = run_process,
                 timeout_s: float = FFMPEG_VIDEO_TIMEOUT_S) -> None:
        self.runner = runner
        self.timeout_s = timeout_s

    async def to_video_note(self, video: bytes) -> bytes:
        if not video:
            raise VideoError("empty video")
        # MP4 needs seekable files on both sides (the index is written last, then moved).
        with tempfile.TemporaryDirectory(prefix="sim-video-note-") as folder:
            src, dst = Path(folder) / "in.mp4", Path(folder) / "out.mp4"
            src.write_bytes(video)
            try:
                code, _out, err = await self.runner(
                    video_note_args(str(src), str(dst)), b"", self.timeout_s)
            except AudioError as exc:      # ffmpeg missing or timed out (shared process runner)
                raise VideoError(str(exc)) from exc
            note = dst.read_bytes() if dst.is_file() else b""
        if code != 0 or not note:
            detail = err.decode("utf-8", "replace").strip()[:200]
            raise VideoError(f"ffmpeg exited {code}: {detail or 'no output'}")
        return note
