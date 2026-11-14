"""«Марина печатает…» / «записывает голосовое…» while a reply is being made."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from sim.adapters.channels.capabilities import CANCEL, RECORD_VOICE, TYPING, Capability
from sim.adapters.db.models import Message
from sim.domain.enums import Direction
from sim.services import chat_activity
from sim.services.chat_activity import mark_read, showing
from tests.conftest import FakePort
from tests.test_voice import FakeEncoder, FakeSpeech, deliver, reply_to, voiced_world


def acting_port() -> FakePort:
    port = FakePort()
    port.capabilities = frozenset({Capability.CHAT_ACTION})
    return port


def shown(actions: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """The statuses shown, without the final clearing."""
    return {a for a in actions if a[1] != CANCEL}


async def test_the_action_is_repeated_while_the_work_goes_on_and_cleared_after(monkeypatch):
    monkeypatch.setattr(chat_activity, "REPEAT_S", 0.01)
    port = acting_port()
    async with showing(port, "chat-1", TYPING):
        await asyncio.sleep(0.05)
    count = len(port.actions)
    assert count >= 3 and shown(port.actions) == {("chat-1", "typing")}
    assert port.actions[-1] == ("chat-1", CANCEL)        # cleared at once when the work ends
    await asyncio.sleep(0.03)
    assert len(port.actions) == count                   # stopped with the block


async def test_a_hanging_step_stops_showing_the_status_after_the_limit(monkeypatch):
    monkeypatch.setattr(chat_activity, "REPEAT_S", 0.01)
    monkeypatch.setattr(chat_activity, "MAX_SHOW_S", 0.03)
    port = acting_port()
    async with showing(port, "chat-1", TYPING):
        await asyncio.sleep(0.05)                       # the limit passes while the work hangs
        count = len(port.actions)
        await asyncio.sleep(0.05)
        assert len(port.actions) == count               # no more «печатает…» past the limit
    assert port.actions[-1] == ("chat-1", CANCEL)


async def test_no_capability_or_no_port_shows_nothing():
    plain = FakePort()
    async with showing(plain, "chat-1", TYPING):
        pass
    async with showing(None, "chat-1", TYPING):
        pass
    assert plain.actions == []


async def test_a_voice_answer_shows_recording_never_typing(container, world, connector):
    await voiced_world(container, world, connector, speech=FakeSpeech(), encoder=FakeEncoder())
    connector.port.capabilities = connector.port.capabilities | {Capability.CHAT_ACTION}
    queued = await reply_to(container, world, client_voice=True)
    assert connector.port.actions and shown(connector.port.actions) == {("chat-1", RECORD_VOICE)}
    await deliver(container, queued.id)                          # and while it is rendered
    assert shown(connector.port.actions) == {("chat-1", RECORD_VOICE)}


async def test_a_text_answer_shows_typing(container, world, connector):
    await voiced_world(container, world, connector, speech=FakeSpeech(), encoder=FakeEncoder())
    connector.port.capabilities = connector.port.capabilities | {Capability.CHAT_ACTION}
    await reply_to(container, world, client_voice=False)         # mirror: text gets text
    assert connector.port.actions and shown(connector.port.actions) == {("chat-1", TYPING)}


async def test_she_reads_the_client_message_before_typing(container, world, connector):
    connector.port.capabilities = frozenset({Capability.CHAT_ACTION, Capability.MARK_READ})
    queued = await reply_to(container, world, client_voice=False)
    async with container.factory() as s:
        last_in = (await s.scalars(select(Message).where(Message.direction == Direction.IN)
                                   .order_by(Message.id.desc()))).first()
    assert queued is not None and last_in.external_id
    assert connector.port.reads == [("chat-1", last_in.external_id)]


async def test_mark_read_needs_the_capability_and_an_id():
    plain, reader = FakePort(), FakePort()
    reader.capabilities = frozenset({Capability.MARK_READ})
    await mark_read(plain, "chat-1", "m-1")
    await mark_read(reader, "chat-1", None)
    await mark_read(None, "chat-1", "m-1")
    await mark_read(reader, "chat-1", "m-1")
    assert plain.reads == [] and reader.reads == [("chat-1", "m-1")]


