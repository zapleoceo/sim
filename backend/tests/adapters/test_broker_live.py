"""BrokerLive over a fake WebSocket: start/ready, events, hang-up summary, refusals."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.live import (
    BrokerLive,
    event_of,
    live_url,
    refusal_error,
    start_message,
)
from sim.ports import LiveEvent, PortError
from tests.adapters.helpers import PROJECT_KEY

CLOSE = object()        # the server closed the socket normally
HANG = object()         # the server never answers `start`
READY = json.dumps({"type": "ready", "model": "gemini/live", "input_rate_hz": 16000,
                    "output_rate_hz": 24000, "max_seconds": 300})
DONE = {"type": "done", "transcript": [{"role": "user", "text": "алло"}], "duration_s": 12.5,
        "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.02, "end_reason": "caller_ended"}


class FakeSocket:
    """The Broker side of /v1/live: `first` answers the start message, `inbox` feeds the
    reader, `on_end` is what the Broker sends after {"type": "end"} (then it closes)."""

    def __init__(self, first: Any = READY, *, on_end: Sequence[Any] | None = ()) -> None:
        self.first = first
        self.on_end = on_end
        self.sent: list[Any] = []
        self.closed = False
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()

    def push(self, *items: Any) -> None:
        for item in items:
            self.inbox.put_nowait(item)

    async def recv(self) -> Any:
        if self.first is HANG:
            await asyncio.Event().wait()
        if isinstance(self.first, BaseException):
            raise self.first
        return self.first

    async def send(self, message: Any) -> None:
        if self.closed:
            raise ConnectionError("socket closed")
        self.sent.append(message)
        if (isinstance(message, str) and self.on_end is not None
                and json.loads(message).get("type") == "end"):
            self.push(*self.on_end, CLOSE)

    async def close(self) -> None:
        self.closed = True
        self.push(CLOSE)

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> Any:
        item = await self.inbox.get()
        if item is CLOSE:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item


class Connector:
    def __init__(self, socket: FakeSocket | BaseException) -> None:
        self.socket = socket
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, url: str, headers: dict[str, str]) -> FakeSocket:
        self.calls.append((url, headers))
        if isinstance(self.socket, BaseException):
            raise self.socket
        return self.socket


def live(connector: Connector, **kwargs: Any) -> BrokerLive:
    return BrokerLive("https://broker.test/", PROJECT_KEY, connect=connector, **kwargs)


async def open_session(connector: Connector, **kwargs: Any) -> Any:
    return await live(connector, **kwargs).open(
        model="gemini/live", system="Ты Марина.", voice="Vindemiatrix", language="ru-RU",
        greeting="", max_seconds=300)


async def collect(session: Any) -> list[LiveEvent]:
    return [event async for event in session.events()]


async def test_open_sends_start() -> None:
    socket = FakeSocket()
    connector = Connector(socket)
    session = await open_session(connector)
    assert connector.calls == [("wss://broker.test/v1/live", {"X-Project-Key": PROJECT_KEY})]
    assert json.loads(socket.sent[0]) == {
        "type": "start", "system": "Ты Марина.", "workflow": "sim-call", "model": "gemini/live",
        "voice": "Vindemiatrix", "language": "ru-RU", "max_seconds": 300}
    assert session.ready["output_rate_hz"] == 24000
    await session.finish()


async def test_conversation_events_and_summary() -> None:
    socket = FakeSocket(on_end=[json.dumps(DONE)])
    session = await open_session(Connector(socket))
    await session.send_audio(b"\x01\x00" * 160)
    await session.send_audio(b"")
    socket.push(b"\x02\x00" * 240,
                json.dumps({"type": "transcript", "role": "user", "text": "алло"}),
                json.dumps({"type": "transcript", "role": "assistant", "text": "привет"}),
                json.dumps({"type": "interrupted"}),
                json.dumps({"type": "turn_complete"}),
                "not json",
                json.dumps({"type": "error", "code": "upstream", "detail": "glitch"}))
    summary = await session.finish()
    events = await collect(session)
    assert [event.kind for event in events] == [
        "audio", "transcript_in", "transcript_out", "interrupted", "error", "done"]
    assert events[0].audio == b"\x02\x00" * 240
    assert events[1].text == "алло"
    assert events[2].text == "привет"
    assert events[4].data == {"code": "upstream"}
    assert events[5].data["cost_usd"] == 0.02
    assert summary == DONE
    assert socket.sent[1] == b"\x01\x00" * 160
    assert json.loads(socket.sent[-1]) == {"type": "end"}
    assert socket.closed
    assert await session.finish() == DONE           # idempotent
    await session.send_audio(b"\x01\x00")           # ignored after hang-up
    assert socket.sent[-1] != b"\x01\x00"
    assert await collect(session) == []             # later readers stop at once


async def test_dropped_socket_ends_the_stream() -> None:
    socket = FakeSocket()
    session = await open_session(Connector(socket))
    socket.push(b"\x00\x00", ConnectionResetError("reset"))
    events = await collect(session)
    assert [event.kind for event in events] == ["audio", "error"]
    assert "ConnectionResetError" in events[1].text
    assert await session.finish() == {}


async def test_finish_without_summary_times_out() -> None:
    socket = FakeSocket(on_end=None)
    session = await open_session(Connector(socket), done_timeout_s=0.05)
    assert await session.finish() == {}
    assert socket.closed
    assert await collect(session) == []


async def test_send_after_server_close_raises() -> None:
    socket = FakeSocket()
    session = await open_session(Connector(socket), done_timeout_s=0.05)
    socket.closed = True
    with pytest.raises(PortError, match="send failed"):
        await session.send_audio(b"\x00\x00")
    await session.finish()


@pytest.mark.parametrize(("code", "retryable"), [(4503, True), (4429, True), (502, True),
                                                 (4422, False), (4403, False), ("bad", False)])
async def test_refusal(code: Any, retryable: bool) -> None:
    socket = FakeSocket(json.dumps({"type": "error", "code": code, "detail": "nope"}))
    with pytest.raises(PortError, match="refused") as info:
        await open_session(Connector(socket))
    assert info.value.retryable is retryable
    assert socket.closed


async def test_connect_failure_is_retryable() -> None:
    with pytest.raises(PortError, match="connect failed") as info:
        await open_session(Connector(OSError("network down")))
    assert info.value.retryable is True


async def test_no_answer_to_start_is_retryable() -> None:
    socket = FakeSocket(HANG)
    with pytest.raises(PortError, match="no answer to start") as info:
        await open_session(Connector(socket), ready_timeout_s=0.05)
    assert info.value.retryable is True
    assert socket.closed


async def test_garbage_answer_to_start() -> None:
    socket = FakeSocket(b"\x00\x01")
    with pytest.raises(PortError, match="refused") as info:
        await open_session(Connector(socket))
    assert info.value.retryable is False


def test_from_http_uses_base_url_and_key() -> None:
    http = BrokerHttp("http://localhost:8080", "k")
    adapter = BrokerLive.from_http(http)
    assert adapter.url == "ws://localhost:8080/v1/live"
    assert adapter.project_key == "k"


def test_live_url() -> None:
    assert live_url("https://aib.zapleo.com/") == "wss://aib.zapleo.com/v1/live"
    assert live_url("http://broker:8000") == "ws://broker:8000/v1/live"
    assert live_url("wss://already") == "wss://already/v1/live"


def test_start_message_leaves_out_empty_fields() -> None:
    assert start_message(model="", system="s", voice="", language="", greeting="",
                         max_seconds=0, workflow="w") == {
        "type": "start", "system": "s", "workflow": "w"}
    assert start_message(model="m", system="s", voice="Kore", language="ru-RU",
                         greeting="Привет", max_seconds=540, workflow="w")["greeting"] == "Привет"


def test_event_of() -> None:
    assert event_of({"type": "transcript", "role": "system", "text": "x"}) is None
    assert event_of({"type": "turn_complete"}) is None
    assert event_of({"type": "interrupted"}) == LiveEvent("interrupted")


def test_refusal_error_without_detail() -> None:
    error = refusal_error({})
    assert error.retryable is False
    assert "no answer" in str(error)
