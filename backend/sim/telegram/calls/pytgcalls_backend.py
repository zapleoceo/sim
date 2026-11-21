"""Private Telegram calls of a userbot account over py-tgcalls / ntgcalls (kept from v1, where
it works in production with py-tgcalls 3.0).

Telethon carries no VoIP; py-tgcalls attaches to the SAME Telethon client (one session must not
be connected twice) and drives ntgcalls. Audio is raw PCM both ways (`ExternalMedia.AUDIO` out
via `send_frame`, `RecordStream` in via StreamFrames updates).

Picking up is bounded: py-tgcalls waits `ANSWER_TIMEOUT_S` for the caller's side to confirm the
key exchange, and a caller who hangs up meanwhile cancels the pending answer at once (py-tgcalls
would otherwise keep the chat's lock until its timeout and a quick redial would wait).

Everything py-tgcalls is reached through `lib` (loaded lazily by `load_pytgcalls`), so the
module imports without the native library and tests drive it with a fake."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from sim.ports import Peer
from sim.telegram.calls.backend import CallGone, CallHandler
from sim.telegram.peers import peer_from_user

log = logging.getLogger(__name__)

CALL_RATE_HZ = 48_000           # Telegram calls run at 48 kHz; ntgcalls frames are 10 ms
ANSWER_TIMEOUT_S = 20           # the caller's confirmation of a picked-up call (CallConfig.timeout)
_ENDED = object()


def load_pytgcalls() -> SimpleNamespace:  # pragma: no cover - needs the native ntgcalls wheel
    from pytgcalls import PyTgCalls
    from pytgcalls.types import (
        CallConfig,
        ChatUpdate,
        Device,
        Direction,
        ExternalMedia,
        MediaStream,
        RecordStream,
        StreamFrames,
    )
    from pytgcalls.types.raw import AudioParameters

    return SimpleNamespace(
        PyTgCalls=PyTgCalls, CallConfig=CallConfig, ChatUpdate=ChatUpdate, Device=Device,
        Direction=Direction, ExternalMedia=ExternalMedia, MediaStream=MediaStream,
        RecordStream=RecordStream, StreamFrames=StreamFrames, AudioParameters=AudioParameters,
    )


def has_status(status: Any, flag: Any) -> bool:
    """ChatUpdate.Status is a flag set; compare as flags, falling back to equality."""
    try:
        return bool(status & flag)
    except TypeError:
        return bool(status == flag)


class TelegramCall:
    """`IncomingCall` over one py-tgcalls private call."""

    def __init__(self, backend: PyTgCallsBackend, chat_id: int) -> None:
        self.backend = backend
        self.chat_id = chat_id
        self.external_id = str(chat_id)
        self.rate_hz = backend.rate_hz
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.over = asyncio.Event()     # the call ended, whoever ended it
        self.answered = False           # we started picking up: the call is ours to hang up

    @property
    def ended(self) -> bool:
        return self.over.is_set()

    async def peer(self) -> Peer:
        """The caller as a Peer; Telethon has the user cached from the call update."""
        try:
            return peer_from_user(await self.backend.client.get_entity(self.chat_id))
        except Exception as exc:
            log.warning("caller %s not resolved: %s", self.chat_id, type(exc).__name__)
            return Peer(external_id=self.external_id)

    async def decline(self) -> None:
        """Rejects a ringing call (py-tgcalls discards a private call it has not joined)."""
        self.remote_ended()
        self.backend.forget(self)
        with contextlib.suppress(Exception):
            await self.backend.calls.leave_call(self.chat_id)

    async def answer(self) -> None:
        if self.ended:
            raise CallGone("the caller hung up before the call was picked up")
        self.answered = True
        accept = asyncio.ensure_future(self.accept())
        gone = asyncio.ensure_future(self.over.wait())
        try:
            await asyncio.wait({accept, gone}, return_when=asyncio.FIRST_COMPLETED)
            picked_up = accept.done()
            if picked_up:
                await accept
        finally:
            for task in (accept, gone):
                task.cancel()
            await asyncio.gather(accept, gone, return_exceptions=True)
        if not picked_up:
            # A cancelled accept may have left a half-made call in ntgcalls, and py-tgcalls
            # would then take the next ring of this chat for a call it is already in.
            with contextlib.suppress(Exception):
                await self.backend.calls.leave_call(self.chat_id)
            raise CallGone("the caller hung up while the call was being picked up")

    async def accept(self) -> None:
        lib, calls = self.backend.lib, self.backend.calls
        params = lib.AudioParameters(self.rate_hz, 1)
        await calls.play(self.chat_id, lib.MediaStream(lib.ExternalMedia.AUDIO, params),
                         lib.CallConfig(timeout=self.backend.answer_timeout_s))
        await calls.record(self.chat_id, lib.RecordStream(True, params))

    def feed(self, pcm: bytes) -> None:
        if not self.ended:
            self.inbox.put_nowait(pcm)

    def remote_ended(self) -> None:
        if not self.ended:
            self.over.set()
            self.inbox.put_nowait(_ENDED)

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            item = await self.inbox.get()
            if item is _ENDED:
                return
            yield item

    async def play(self, pcm: bytes) -> None:
        if not self.ended:
            await self.backend.calls.send_frame(self.chat_id, self.backend.lib.Device.MICROPHONE,
                                                pcm)

    async def hangup(self) -> None:
        """Ends an answered call; an unanswered one is only forgotten."""
        already = self.ended
        self.remote_ended()
        self.backend.forget(self)
        if self.answered and not already:
            with contextlib.suppress(Exception):
                await self.backend.calls.leave_call(self.chat_id)


class PyTgCallsBackend:
    """`CallBackend`: attaches py-tgcalls to a connected Telethon client and turns its updates
    into calls."""

    def __init__(self, client: Any, *, lib: SimpleNamespace | None = None,
                 rate_hz: int = CALL_RATE_HZ, answer_timeout_s: int = ANSWER_TIMEOUT_S) -> None:
        self.client = client
        self.lib = lib or load_pytgcalls()
        self.calls = self.lib.PyTgCalls(client)
        self.rate_hz = rate_hz
        self.answer_timeout_s = answer_timeout_s
        self.on_call: CallHandler | None = None
        self.active: dict[int, TelegramCall] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.started = False

    async def start(self, on_call: CallHandler) -> None:
        self.on_call = on_call
        if self.started:
            return
        self.calls.on_update()(self.on_update)
        await self.calls.start()
        self.started = True

    def forget(self, call: TelegramCall) -> None:
        """Only its own entry: the same person may already be ringing again."""
        if self.active.get(call.chat_id) is call:
            del self.active[call.chat_id]

    async def on_update(self, _client: Any, update: Any) -> None:
        lib = self.lib
        if isinstance(update, lib.StreamFrames):
            call = self.active.get(int(update.chat_id))
            incoming = (update.direction == lib.Direction.INCOMING
                        and update.device == lib.Device.MICROPHONE)
            if call is not None and incoming:
                for frame in update.frames:
                    call.feed(bytes(frame.frame))
            return
        if not isinstance(update, lib.ChatUpdate):
            return
        chat_id, status, statuses = int(update.chat_id), update.status, lib.ChatUpdate.Status
        if has_status(status, statuses.INCOMING_CALL):
            if chat_id in self.active or chat_id <= 0:
                return
            call = TelegramCall(self, chat_id)
            self.active[chat_id] = call
            task = asyncio.ensure_future(self.run(call))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        elif any(has_status(status, getattr(statuses, name, 0))
                 for name in ("DISCARDED_CALL", "LEFT_CALL", "BUSY_CALL")):
            call = self.active.pop(chat_id, None)
            if call is not None:
                call.remote_ended()

    async def run(self, call: TelegramCall) -> None:
        try:
            if self.on_call is not None:
                await self.on_call(call)
        except Exception:
            log.exception("call handler failed")
        finally:
            await call.hangup()
