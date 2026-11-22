"""Everything a service needs from the outside world, built once per process (wiring).

Procrastinate tasks are plain functions, so the process entrypoint registers the process-wide
`Deps` with `set_deps()` and tasks read it with `get_deps()`."""
from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.config import Settings
from sim.ports import (
    ClockPort,
    ImageGenPort,
    LinkVideoPort,
    LivePort,
    LlmPort,
    MediaStoragePort,
    MediaToolsPort,
    SpeechPort,
    TranscriberPort,
    VideoGenPort,
)
from sim.services.queue import TaskQueue


def system_uniform() -> Callable[[], float]:
    return secrets.SystemRandom().random


@dataclass
class Deps:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    clock: ClockPort
    llm: LlmPort
    transcriber: TranscriberPort
    speech: SpeechPort
    image_gen: ImageGenPort
    video_gen: VideoGenPort
    live: LivePort
    storage: MediaStoragePort
    tools: MediaToolsPort
    tasks: TaskQueue
    # A uniform number in [0, 1) for human-like delays (docs/attention.md); tests fix it.
    uniform: Callable[[], float] = field(default_factory=system_uniform)
    # Clips by their links (docs/reply.md, «Ролик по ссылке»); None = links stay text.
    links: LinkVideoPort | None = None


_registry: dict[str, Deps] = {}


def set_deps(deps: Deps) -> None:
    """Called once by the process entrypoint (worker / telegram / api) after wiring."""
    _registry["deps"] = deps


def get_deps() -> Deps:
    try:
        return _registry["deps"]
    except KeyError:
        raise RuntimeError("Deps are not wired: call set_deps() in the entrypoint") from None


def reset_deps() -> None:
    """Tests only: forget the registered Deps."""
    _registry.clear()
