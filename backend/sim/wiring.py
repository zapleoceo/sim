"""The one place where ports get their adapters. Every process (api, worker, telegram) calls
`wire()` and gets the same `Deps`; nothing else constructs adapters."""
from __future__ import annotations

from dataclasses import dataclass

import procrastinate
from sqlalchemy.ext.asyncio import AsyncEngine

from sim.adapters.broker import BrokerAdapters
from sim.adapters.media_tools import FfmpegMediaTools
from sim.adapters.storage import LocalMediaStorage
from sim.channels.routing import DbChannelRouter
from sim.clock import SystemClock
from sim.config import Settings
from sim.db.session import make_engine, make_sessionmaker
from sim.services.deps import Deps
from sim.tasks.app import POOL_SIZE, make_app
from sim.tasks.queue import ProcrastinateQueue


@dataclass
class Wired:
    deps: Deps
    tasks_app: procrastinate.App
    broker: BrokerAdapters
    engine: AsyncEngine

    async def aclose(self) -> None:
        await self.broker.aclose()
        await self.engine.dispose()


def wire(settings: Settings, *, pool_size: int = 5, tasks_pool_size: int = POOL_SIZE) -> Wired:
    engine = make_engine(settings.database_url, pool_size=pool_size)
    broker = BrokerAdapters.from_settings(settings)
    tasks_app = make_app(settings, pool_size=tasks_pool_size)
    sessions = make_sessionmaker(engine)
    deps = Deps(
        settings=settings,
        sessions=sessions,
        clock=SystemClock(),
        llm=broker.llm,
        transcriber=broker.transcriber,
        speech=broker.speech,
        image_gen=broker.image,
        video_gen=broker.video,
        live=broker.live,
        storage=LocalMediaStorage(settings.media_dir),
        tools=FfmpegMediaTools(),
        # Sends, receipts and statuses go to the queue of the chat's messenger.
        tasks=ProcrastinateQueue(tasks_app, router=DbChannelRouter(sessions)),
    )
    return Wired(deps=deps, tasks_app=tasks_app, broker=broker, engine=engine)
