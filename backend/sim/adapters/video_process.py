"""Resource-contained video decoding. Never use audio's unbounded communicate()."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from sim.adapters.audio import AudioError

STDERR_LIMIT = 16 * 1024
PROBE_LIMIT = 16 * 1024
FRAME_LIMIT = 1024 * 1024
PIPE_BUFFER = 8192
CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1",
             "OPENBLAS_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "1"}


async def read_bounded(reader: asyncio.StreamReader, maximum: int) -> bytes:
    data = bytearray()
    while chunk := await reader.read(min(PIPE_BUFFER, maximum - len(data) + 1)):
        if len(data) + len(chunk) > maximum:
            raise AudioError("video decoder output limit exceeded")
        data.extend(chunk)
    return bytes(data)


async def discard(reader: asyncio.StreamReader) -> None:
    while await reader.read(PIPE_BUFFER):
        pass


async def terminate(proc: asyncio.subprocess.Process) -> None:
    # The leader may already have exited while another group member retains a
    # pipe. Always terminate the session group, not only a live leader.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    # Drain after killing, without accumulating output. wait() alone can hang on
    # full PIPE transports. This task is shielded even from repeated cancellation.
    await asyncio.gather(discard(proc.stdout), discard(proc.stderr), proc.wait())


async def cleanup(proc: asyncio.subprocess.Process, readers: list[asyncio.Task]) -> None:
    await asyncio.gather(*readers, return_exceptions=True)
    await terminate(proc)


async def run_video_process(args: Sequence[str], data: bytes, limit_s: float) -> tuple[int, bytes, bytes]:
    if sys.platform != "linux" or not args or args[0] not in ("ffprobe", "ffmpeg") or data:
        raise AudioError("isolated video decoder unavailable")
    helper = Path(__file__).with_name("_video_limits.py")
    try:
        proc = await asyncio.create_subprocess_exec(  # noqa: S603
            sys.executable, "-I", str(helper), *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=PIPE_BUFFER,
            env=CLEAN_ENV, cwd="/", start_new_session=True, close_fds=True,
        )
    except OSError as exc:
        raise AudioError("isolated video decoder unavailable") from exc
    readers = [asyncio.create_task(read_bounded(proc.stdout, PROBE_LIMIT if args[0] == "ffprobe" else FRAME_LIMIT)),
               asyncio.create_task(read_bounded(proc.stderr, STDERR_LIMIT)),
               asyncio.create_task(proc.wait())]
    try:
        async with asyncio.timeout(min(limit_s, 8)):
            out, err, code = await asyncio.gather(*readers)
        return code, out, err
    except BaseException as exc:
        for task in readers:
            task.cancel()
        reaper = asyncio.create_task(cleanup(proc, readers))
        while not reaper.done():
            try:
                await asyncio.shield(reaper)
            except asyncio.CancelledError:
                continue
        reaper.result()
        if isinstance(exc, TimeoutError):
            raise AudioError("video decoder timed out") from exc
        raise
