"""Offline process containment, no real media, models, accounts or secrets."""
import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest

from sim.adapters import _video_limits, video_process
from sim.adapters.audio import AudioError


class Process:
    pid = 123456
    returncode = None

    def __init__(self):
        self.stdout = asyncio.StreamReader(limit=video_process.PIPE_BUFFER)
        self.stderr = asyncio.StreamReader(limit=video_process.PIPE_BUFFER)
        self.exited = asyncio.Event()
        self.spawned = asyncio.Event()
        self.reaped = False

    async def wait(self):
        await self.exited.wait()
        self.reaped = True
        return self.returncode

    def kill(self, pid, sig):
        assert pid == self.pid and sig == video_process.signal.SIGKILL
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.exited.set()


def setup(monkeypatch):
    proc = Process()

    async def create(*args, **kwargs):
        proc.spawned.set()
        return proc

    spawn = AsyncMock(side_effect=create)
    monkeypatch.setattr(video_process.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(video_process.os, "killpg", proc.kill)
    return proc, spawn


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("run", [video_process.run_video_process, video_process.run_video_encoder])
async def test_overflow_kills_and_reaps_without_unbounded_communicate(monkeypatch, stream, run):
    proc, spawn = setup(monkeypatch)
    getattr(proc, stream).feed_data(b"x" * (video_process.PROBE_LIMIT + 1))
    with pytest.raises(AudioError, match="output limit"):
        await run(["ffprobe", "synthetic"], b"", 1)
    assert proc.reaped and proc.returncode == -9
    args, options = spawn.call_args
    assert args[1] == "-I" and args[2].endswith("_video_limits.py")
    assert options["env"] == _video_limits.CLEAN_ENV
    assert options["start_new_session"] and options["cwd"] == "/"
    assert options["close_fds"]
    assert options["stdin"] == asyncio.subprocess.DEVNULL
    assert options["limit"] == video_process.PIPE_BUFFER


@pytest.mark.parametrize("run", [video_process.run_video_process, video_process.run_video_encoder])
async def test_timeout_kills_and_reaps(monkeypatch, run):
    proc, _ = setup(monkeypatch)
    with pytest.raises(AudioError, match="timed out"):
        await run(["ffprobe"], b"", 0.01)
    assert proc.reaped and proc.returncode == -9


async def test_encoded_mp4_pipe_overflow_is_killed_before_return(monkeypatch):
    proc, _ = setup(monkeypatch)
    proc.stdout.feed_data(b"x" * (video_process.ENCODE_LIMIT + 1))
    with pytest.raises(AudioError, match="output limit"):
        await video_process.run_video_encoder(["ffmpeg"], b"", 1)
    assert proc.reaped and proc.returncode == -9


async def test_exited_leader_with_open_pipes_still_terminates_group(monkeypatch):
    proc, _ = setup(monkeypatch)
    proc.returncode = 0
    proc.exited.set()
    with pytest.raises(AudioError, match="timed out"):
        await video_process.run_video_process(["ffprobe"], b"", 0.01)
    assert proc.reaped and proc.returncode == -9


@pytest.mark.parametrize("run", [video_process.run_video_process, video_process.run_video_encoder])
async def test_repeated_cancellation_still_reaps(monkeypatch, run):
    proc, _ = setup(monkeypatch)
    release = asyncio.Event()
    original = video_process.terminate

    async def delayed_reaper(process):
        await release.wait()
        await original(process)

    monkeypatch.setattr(video_process, "terminate", delayed_reaper)
    task = asyncio.create_task(run(["ffmpeg"], b"", 1))
    await proc.spawned.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.reaped and proc.returncode == -9


async def test_bounded_success_and_nonzero_exit_preserved(monkeypatch):
    proc, _ = setup(monkeypatch)
    proc.stdout.feed_data(b"result")
    proc.stderr.feed_data(b"error")
    proc.returncode = 7
    proc.stdout.feed_eof()
    proc.stderr.feed_eof()
    proc.exited.set()
    assert await video_process.run_video_process(["ffprobe"], b"", 1) == (7, b"result", b"error")


@pytest.mark.parametrize("encode", [False, True])
def test_bootstrap_limits_applied_before_fixed_exec_with_clean_environment(monkeypatch, encode):
    calls = []
    monkeypatch.setattr(_video_limits.resource, "setrlimit", lambda kind, values: calls.append((kind, values)))

    def execute(path, args, env):
        limits = dict(calls)
        r = _video_limits.resource
        assert limits[r.RLIMIT_AS] == (512 * 1024 * 1024,) * 2
        assert limits[r.RLIMIT_CPU] == ((30, 30) if encode else (6, 6))
        assert limits[r.RLIMIT_STACK] == (2 * 1024 * 1024,) * 2
        assert limits[r.RLIMIT_FSIZE] == (0, 0) and limits[r.RLIMIT_CORE] == (0, 0)
        assert limits[r.RLIMIT_NOFILE] == (32, 32)
        assert path == "/usr/bin/ffprobe" and args == ["ffprobe", "synthetic"]
        assert env == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "1"}

    monkeypatch.setattr(_video_limits.os, "execve", execute)
    _video_limits.launch((["--encode"] if encode else []) + ["ffprobe", "synthetic"])
    with pytest.raises(ValueError):
        _video_limits.launch(["sh", "-c", "echo forbidden"])


async def test_unsupported_platform_fails_closed_before_spawn(monkeypatch):
    monkeypatch.setattr(video_process.sys, "platform", "win32")
    proc, spawn = setup(monkeypatch)
    with pytest.raises(AudioError, match="unavailable"):
        await video_process.run_video_process(["ffmpeg"], b"", 1)
    assert not spawn.called and not proc.reaped


async def test_kernel_memory_limit_in_disposable_child():
    # Real OS readback and allocation refusal, only in CI. No codec/provider input.
    script = """
import json, resource, runpy, sys
scope = runpy.run_path(sys.argv[1])
def check(path, args, env):
    blocked = False
    try:
        bytearray(1024 * 1024 * 1024)
    except MemoryError:
        blocked = True
    print(json.dumps({'as': resource.getrlimit(resource.RLIMIT_AS),
        'cpu': resource.getrlimit(resource.RLIMIT_CPU), 'blocked': blocked}))
scope['os'].execve = check
scope['launch'](['ffprobe'])
"""
    proc = await asyncio.create_subprocess_exec(  # noqa: S603
        sys.executable, "-I", "-c", script, _video_limits.__file__,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=video_process.CLEAN_ENV,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 5)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
    assert proc.returncode == 0, err
    observed = json.loads(out)
    assert observed == {'as': [512 * 1024 * 1024] * 2, 'cpu': [6, 6], 'blocked': True}
