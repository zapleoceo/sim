"""Live inbox: committed dialog changes reach the open /api/events streams (ids only)."""
from __future__ import annotations

import asyncio
import json

from sim.adapters.db.models import Message
from sim.adapters.db.session import transaction
from sim.api.routers.events import stream
from sim.services.live import HUB, LiveHub
from tests.conftest import inbound
from tests.test_ingest import ingest


async def test_hub_fans_out_and_drops_closed_subscribers():
    hub = LiveHub()
    async with hub.subscribe() as a, hub.subscribe() as b:
        hub.publish([3, 1, 3])
        assert await a.get() == [1, 3] and await b.get() == [1, 3]
    assert hub.subscribers == 0
    hub.publish([1])                          # nobody listens: nothing to do


async def test_a_committed_inbound_message_is_announced(container, world):
    async with HUB.subscribe() as queue:
        res = await ingest(container, world.channel.id, inbound("Привет", ext="live-1"))
        ids = await asyncio.wait_for(queue.get(), 1)
    assert res.dialog.id in ids


async def test_a_rolled_back_change_is_not_announced(container, world):
    await ingest(container, world.channel.id, inbound("Привет", ext="live-2"))
    async with HUB.subscribe() as queue:
        try:
            async with transaction(container.factory) as s:
                message = (await s.execute(Message.__table__.select())).first()
                row = await s.get(Message, message.id)
                row.text = "изменено"
                await s.flush()
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        assert queue.empty()


class FakeRequest:
    def __init__(self) -> None:
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > 3


async def test_the_stream_sends_dialog_events_and_heartbeats():
    hub = LiveHub()
    gen = stream(FakeRequest(), hub, heartbeat_s=0.01)
    assert (await gen.__anext__()).startswith("retry:")
    pending = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    hub.publish([9])
    event = await pending
    assert event.startswith("event: dialogs") and json.loads(event.split("data: ")[1]) == {"dialogs": [9]}
    assert await gen.__anext__() == ": ping\n\n"
    await gen.aclose()


async def test_events_need_a_login(api):
    assert (await api.get("/api/events")).status_code == 401
