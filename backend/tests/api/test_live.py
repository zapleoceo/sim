"""Live updates: one listener per process fanned out to every SSE subscriber."""
from __future__ import annotations

import asyncio

from sim.api.live import EventHub, Publish, format_event, plain_dsn, sse_stream
from tests.api.helpers import ApiClient

PAYLOAD = '{"chat_id": 1, "event_id": 42, "kind": "message_in"}'


class FakeListener:
    """Stands in for Postgres LISTEN: remembers `publish` and counts how often it started."""

    def __init__(self) -> None:
        self.starts = 0
        self.publish: Publish | None = None
        self.ready = asyncio.Event()

    async def __call__(self, publish: Publish) -> None:
        self.starts += 1
        self.publish = publish
        self.ready.set()
        await asyncio.Event().wait()


def test_plain_dsn_strips_the_driver() -> None:
    assert plain_dsn("postgresql+psycopg://u:p@db:5432/sim") == "postgresql://u:p@db:5432/sim"
    assert plain_dsn("postgresql://u@db/sim") == "postgresql://u@db/sim"


def test_format_event_keeps_payload() -> None:
    assert format_event(PAYLOAD) == f"data: {PAYLOAD}\n\n"
    assert format_event("a\nb") == "data: a\ndata: b\n\n"


async def test_hub_fans_out_to_every_subscriber_with_one_listener() -> None:
    listener = FakeListener()
    hub = EventHub(listener)
    async with hub.subscribe() as first, hub.subscribe() as second:
        await asyncio.wait_for(listener.ready.wait(), 1)
        assert listener.publish is not None
        listener.publish(PAYLOAD)
        assert await asyncio.wait_for(first.get(), 1) == PAYLOAD
        assert await asyncio.wait_for(second.get(), 1) == PAYLOAD
        assert hub.subscriber_count == 2
    assert hub.subscriber_count == 0
    assert listener.starts == 1
    await hub.close()


async def test_slow_subscriber_drops_oldest_instead_of_blocking() -> None:
    hub = EventHub(FakeListener())
    async with hub.subscribe() as queue:
        for i in range(queue.maxsize + 5):
            hub.publish(str(i))
        assert queue.qsize() == queue.maxsize
        assert queue.get_nowait() == "5"
    await hub.close()


async def test_sse_stream_sends_retry_events_and_heartbeat() -> None:
    hub = EventHub(FakeListener())
    stream = sse_stream(hub, heartbeat_s=0.05)
    assert await anext(stream) == "retry: 3000\n\n"
    assert hub.subscriber_count == 1
    hub.publish(PAYLOAD)
    assert await anext(stream) == f"data: {PAYLOAD}\n\n"
    assert await asyncio.wait_for(anext(stream), 1) == ": ping\n\n"
    await stream.aclose()
    assert hub.subscriber_count == 0
    await hub.close()


async def test_events_route_needs_login(client: ApiClient) -> None:
    response = await client.get("/api/events")
    assert response.status_code == 401
