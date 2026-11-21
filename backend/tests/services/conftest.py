"""Fixtures for service tests: fake ports, Deps, and a session factory bound to the test's
connection, so a service's own transactions are SAVEPOINTs inside the rolled-back test
transaction and the test sees their rows."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.clock import FixedClock
from sim.config import Settings
from sim.services.deps import Deps
from tests.fakes.ai import (
    FakeImageGen,
    FakeLive,
    FakeLlm,
    FakeMediaTools,
    FakeSpeech,
    FakeTranscriber,
    FakeVideoGen,
    MemoryStorage,
    RecordingQueue,
)


@dataclass
class Fakes:
    llm: FakeLlm
    transcriber: FakeTranscriber
    speech: FakeSpeech
    image_gen: FakeImageGen
    video_gen: FakeVideoGen
    live: FakeLive
    storage: MemoryStorage
    tools: FakeMediaTools
    tasks: RecordingQueue


@pytest.fixture
def fakes() -> Fakes:
    return Fakes(llm=FakeLlm(), transcriber=FakeTranscriber(), speech=FakeSpeech(),
                 image_gen=FakeImageGen(), video_gen=FakeVideoGen(), live=FakeLive(),
                 storage=MemoryStorage(), tools=FakeMediaTools(), tasks=RecordingQueue())


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", context_budget_tokens=60_000, context_keep_recent_events=60)


@pytest.fixture
def sessions(db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db.bind, expire_on_commit=False,
                              join_transaction_mode="create_savepoint")


def make_deps(settings: Settings, sessions: async_sessionmaker[AsyncSession],
              clock: FixedClock, fakes: Fakes) -> Deps:
    return Deps(settings=settings, sessions=sessions, clock=clock, llm=fakes.llm,
                transcriber=fakes.transcriber, speech=fakes.speech,
                image_gen=fakes.image_gen, video_gen=fakes.video_gen, live=fakes.live,
                storage=fakes.storage, tools=fakes.tools, tasks=fakes.tasks)


@pytest.fixture
def deps(settings: Settings, sessions: async_sessionmaker[AsyncSession], clock: FixedClock,
         fakes: Fakes) -> Deps:
    return make_deps(settings, sessions, clock, fakes)
