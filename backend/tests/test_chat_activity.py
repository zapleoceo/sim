"""«Марина печатает…» / «записывает голосовое…» while a reply is being made."""
from __future__ import annotations

import asyncio

from sim.adapters.channels.capabilities import RECORD_VOICE, TYPING, Capability
from sim.services import chat_activity
from sim.services.chat_activity import showing
from tests.conftest import FakePort
from tests.test_voice import FakeEncoder, FakeSpeech, deliver, reply_to, voiced_world


def acting_port() -> FakePort:
    port = FakePort()
    port.capabilities = frozenset({Capability.CHAT_ACTION})
    return port


async def test_the_action_is_repeated_while_the_work_goes_on(monkeypatch):
    monkeypatch.setattr(chat_activity, "REPEAT_S", 0.01)
    port = acting_port()
    async with showing(port, "chat-1", TYPING):
        await asyncio.sleep(0.05)
    count = len(port.actions)
    assert count >= 2 and set(port.actions) == {("chat-1", "typing")}
    await asyncio.sleep(0.03)
    assert len(port.actions) == count                   # stopped with the block


async def test_no_capability_or_no_port_shows_nothing():
    plain = FakePort()
    async with showing(plain, "chat-1", TYPING):
        pass
    async with showing(None, "chat-1", TYPING):
        pass
    assert plain.actions == []


async def test_a_reply_shows_typing_and_a_voice_note_shows_recording(container, world, connector):
    await voiced_world(container, world, connector, speech=FakeSpeech(), encoder=FakeEncoder())
    connector.port.capabilities = connector.port.capabilities | {Capability.CHAT_ACTION}
    queued = await reply_to(container, world, client_voice=True)
    assert ("chat-1", TYPING) in connector.port.actions          # while the model wrote it
    await deliver(container, queued.id)
    assert ("chat-1", RECORD_VOICE) in connector.port.actions    # while the voice was rendered


