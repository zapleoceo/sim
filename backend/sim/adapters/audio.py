"""Audio transcoding for outgoing voice notes.

Gemini TTS returns WAV (24 kHz mono PCM). Telegram shows a voice bubble only for OGG/Opus,
and WhatsApp plays OGG/Opus as a push-to-talk note, so every voice note is converted once
with ffmpeg (installed in the backend image) before it is stored and sent."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

OGG_MIME = "audio/ogg"
FFMPEG_TIMEOUT_S = 60.0
# 32 kbit/s mono Opus at 48 kHz: what the Telegram apps themselves record.
OGG_OPUS_ARGS: tuple[str, ...] = (
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
    "-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k",
    "-application", "voip", "-f", "ogg", "pipe:1",
)

Runner = Callable[[Sequence[str], bytes, float], Awaitable[tuple[int, bytes, bytes]]]


class AudioError(Exception):
    """Transcoding failed (ffmpeg missing, bad input, timeout)."""


async def run_process(args: Sequence[str], data: bytes, limit_s: float) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(  # noqa: S603, S607
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise AudioError(f"{args[0]} unavailable: {type(exc).__name__}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), limit_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AudioError(f"{args[0]} timed out after {limit_s:g}s") from exc
    return proc.returncode or 0, out, err


class OggOpusEncoder:
    """Any audio ffmpeg reads → OGG/Opus voice note."""

    def __init__(self, runner: Runner = run_process, timeout_s: float = FFMPEG_TIMEOUT_S) -> None:
        self.runner = runner
        self.timeout_s = timeout_s

    async def to_voice_note(self, audio: bytes) -> bytes:
        if not audio:
            raise AudioError("empty audio")
        code, out, err = await self.runner(OGG_OPUS_ARGS, audio, self.timeout_s)
        if code != 0 or not out:
            detail = err.decode("utf-8", "replace").strip()[:200]
            raise AudioError(f"ffmpeg exited {code}: {detail or 'no output'}")
        return out
