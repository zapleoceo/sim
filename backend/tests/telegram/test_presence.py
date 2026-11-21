"""Live chat statuses: the one action mapping (sim.domain.presence) and the Presence loop over a
fake messenger with a fake clock (sleeping advances the clock at once)."""
from __future__ import annotations

import asyncio

from sim.domain.presence import (
    MAX_PRESENCE_S,
    THINKING,
    ChatAction,
    busy_with,
    chat_action_for,
    ended_by,
)
from sim.ports import Peer
from sim.telegram.messenger import CHAT_ACTIONS
from sim.telegram.presence import Presence
from tests.fakes.telegram import FakeMessenger

PEER = Peer(external_id="1001", access_hash="555")


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


def make() -> tuple[Presence, FakeMessenger, FakeTime]:
    messenger, time = FakeMessenger(), FakeTime()
    return Presence(messenger, monotonic=time.mono, sleep=time.sleep), messenger, time


def actions(messenger: FakeMessenger) -> list[str]:
    return [action for _, action in messenger.actions]


async def settle(presence: Presence) -> None:
    await asyncio.gather(*presence.running(), return_exceptions=True)


# --- the mapping -----------------------------------------------------------------------------


def test_one_action_per_kind():
    assert chat_action_for("text") == ChatAction.TYPING
    assert chat_action_for("voice") == ChatAction.RECORD_AUDIO
    assert chat_action_for("video_note") == ChatAction.RECORD_ROUND
    assert chat_action_for("photo") == ChatAction.UPLOAD_PHOTO
    assert chat_action_for("video") == ChatAction.UPLOAD_VIDEO
    assert chat_action_for("file") == ChatAction.UPLOAD_DOCUMENT
    assert busy_with("voice").seconds == 30.0
    assert busy_with("video_note").action == ChatAction.RECORD_ROUND
    assert THINKING.action == ChatAction.TYPING


def test_every_action_has_a_telethon_type():
    assert set(CHAT_ACTIONS) == set(ChatAction)


def test_a_message_ends_thinking_or_its_own_work_only():
    assert ended_by("typing", "photo")
    assert ended_by("upload_video", "video")
    assert ended_by("record_round", "video_note")
    assert not ended_by("upload_video", "text")
    assert not ended_by("record_round", "video")


# --- the loop --------------------------------------------------------------------------------


async def test_status_is_sent_at_once_and_refreshed_until_it_expires():
    presence, messenger, time = make()
    await presence.show(1, 7, PEER, "typing", 10)
    assert actions(messenger) == ["typing"]
    await settle(presence)
    assert actions(messenger) == ["typing", "typing", "typing"]
    assert time.sleeps == [4.5, 4.5, 1.0] and time.now == 10.0
    assert presence.action_of(1) is None
    assert messenger.actions[0][0] == 7


async def test_a_new_request_replaces_the_action_and_the_deadline():
    presence, messenger, time = make()
    await presence.show(1, 7, PEER, "typing", 10)
    await presence.show(1, 7, PEER, "record_audio", 20)
    assert presence.action_of(1) == "record_audio"
    assert len(presence.running()) == 1                 # one loop per chat
    await settle(presence)
    assert actions(messenger)[:2] == ["typing", "record_audio"]
    assert set(actions(messenger)[2:]) == {"record_audio"}
    assert time.now == 20.0


async def test_cancel_stops_the_loop_and_clears_the_status():
    presence, messenger, _ = make()
    await presence.show(1, 7, PEER, "upload_video", 200)
    await presence.show(1, 7, PEER, "cancel", 0)
    assert actions(messenger) == ["upload_video", "cancel"]
    assert presence.action_of(1) is None and presence.running() == []


async def test_a_sent_message_stops_only_what_it_finishes():
    presence, _, _ = make()
    await presence.show(1, 7, PEER, "upload_video", 200)
    presence.sent(1, "text")                            # a text while the video is made
    assert presence.action_of(1) == "upload_video"
    presence.sent(1, "video")
    assert presence.action_of(1) is None
    await presence.show(1, 7, PEER, "typing", 40)
    presence.sent(1, "photo")                           # thinking ends with any message
    assert presence.action_of(1) is None and presence.running() == []


async def test_a_long_status_is_bounded():
    presence, _, time = make()
    await presence.show(1, 7, PEER, "record_round", 1000)
    await settle(presence)
    assert time.now == MAX_PRESENCE_S


async def test_chats_are_independent_and_close_stops_everything():
    presence, _, _ = make()
    await presence.show(1, 7, PEER, "typing", 100)
    await presence.show(2, 7, PEER, "upload_photo", 100)
    presence.sent(1, "text")
    assert presence.action_of(2) == "upload_photo"
    await presence.close()
    assert presence.running() == [] and presence.action_of(2) is None


async def test_a_failing_messenger_never_raises():
    class Broken(FakeMessenger):
        async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
            raise RuntimeError("telegram down")

    time = FakeTime()
    presence = Presence(Broken(), monotonic=time.mono, sleep=time.sleep)
    await presence.show(1, 7, PEER, "typing", 5)
    await settle(presence)
    await presence.show(1, 7, PEER, "cancel", 0)
