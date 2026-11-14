"""Private Telegram voice calls of a userbot account, over py-tgcalls / ntgcalls.

Telethon carries no VoIP; py-tgcalls attaches to the SAME Telethon client (one session must not
be connected twice) and drives ntgcalls, which speaks Telegram's call protocol. Audio is raw
PCM both ways (`ExternalMedia.AUDIO` out via `send_frame`, `RecordStream` in via StreamFrames
updates), so the conversation itself happens in sim.services.calls, not here.

Picking up is bounded: py-tgcalls waits `ANSWER_TIMEOUT_S` for the caller's side to confirm the
key exchange (its own default is 60 s), and a caller who hangs up meanwhile cancels the pending
answer at once. py-tgcalls does not do the second part itself for an incoming call: it would
keep the chat's lock until the timeout, and a quick redial would wait behind the dead call.

Everything py-tgcalls is reached through `lib` (loaded lazily by `load_pytgcalls`), so the
module imports without the native library and tests drive it with a fake."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)

CALL_RATE_HZ = 48_000           # Telegram calls run at 48 kHz; ntgcalls frames are 10 ms
ANSWER_TIMEOUT_S = 20           # the caller's confirmation of a picked-up call (CallConfig.timeout)
_ENDED = object()


class CallGone(Exception):
    """The caller hung up before the call was picked up."""


def load_pytgcalls() -> SimpleNamespace:  # pragma: no cover - needs the native ntgcalls wheel
    from pytgcalls import PyTgCalls  # noqa: PLC0415
    from pytgcalls.types import (  # noqa: PLC0415
        CallConfig,
        ChatUpdate,
        Device,
        Direction,
        ExternalMedia,
        MediaStream,
        RecordStream,
        StreamFrames,
    )
    from pytgcalls.types.raw import AudioParameters  # noqa: PLC0415

    return SimpleNamespace(
        PyTgCalls=PyTgCalls, CallConfig=CallConfig, ChatUpdate=ChatUpdate, Device=Device,
        Direction=Direction,
        ExternalMedia=ExternalMedia, MediaStream=MediaStream, RecordStream=RecordStream,
        StreamFrames=StreamFrames, AudioParameters=AudioParameters,
    )


def has_status(status: Any, flag: Any) -> bool:
    """ChatUpdate.Status is a flag set; compare as flags, falling back to equality."""
    try:
        return bool(status & flag)
    except TypeError:
        return bool(status == flag)


class TelegramCall:
    """IncomingCall over one py-tgcalls private call."""

    def __init__(self, bridge: TelegramCallBridge, chat_id: int) -> None:
        self.bridge = bridge
        self.chat_id = chat_id
        self.external_thread_id = str(chat_id)
        self.rate_hz = bridge.rate_hz
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.over = asyncio.Event()     # the call ended, whoever ended it
        self.answered = False           # we started picking up: the call is ours to hang up

    @property
    def ended(self) -> bool:
        return self.over.is_set()

    async def answer(self) -> None:
        """Picks up, or raises: CallGone when the caller is no longer there, py-tgcalls'
        TimedOutAnswer when the caller's side did not confirm in `answer_timeout_s`."""
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
            # A cancelled accept may have left its half-made call in ntgcalls, and py-tgcalls
            # would then take the next ring of this chat for a call it is already in.
            with contextlib.suppress(Exception):
                await self.bridge.calls.leave_call(self.chat_id)
            raise CallGone("the caller hung up while the call was being picked up")

    async def accept(self) -> None:
        lib, calls = self.bridge.lib, self.bridge.calls
        params = lib.AudioParameters(self.rate_hz, 1)
        await calls.play(self.chat_id, lib.MediaStream(lib.ExternalMedia.AUDIO, params),
                         lib.CallConfig(timeout=self.bridge.answer_timeout_s))
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
            await self.bridge.calls.send_frame(self.chat_id, self.bridge.lib.Device.MICROPHONE, pcm)

    async def hangup(self) -> None:
        """Ends an answered call. A call that was never answered is only forgotten: it keeps
        ringing on the account's other devices, so a person can still pick it up."""
        already = self.ended
        self.remote_ended()
        self.bridge.forget(self)
        if self.answered and not already:
            with contextlib.suppress(Exception):
                await self.bridge.calls.leave_call(self.chat_id)


class TelegramCallBridge:
    """Attaches py-tgcalls to a connected Telethon client and turns its updates into calls."""

    def __init__(
        self, client: Any, on_call: Callable[[TelegramCall], Awaitable[None]], *,
        lib: SimpleNamespace | None = None, rate_hz: int = CALL_RATE_HZ,
        answer_timeout_s: int = ANSWER_TIMEOUT_S,
    ) -> None:
        self.lib = lib or load_pytgcalls()
        self.calls = self.lib.PyTgCalls(client)
        self.on_call = on_call
        self.rate_hz = rate_hz
        self.answer_timeout_s = answer_timeout_s
        self.active: dict[int, TelegramCall] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.started = False

    async def start(self) -> None:
        if self.started:
            return
        self.calls.on_update()(self.on_update)
        await self.calls.start()
        self.started = True

    def forget(self, call: TelegramCall) -> None:
        """Only its own entry: the same person may already be ringing again, and that newer
        call must keep receiving its frames and its hang-up."""
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
            if chat_id in self.active:
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
            await self.on_call(call)
        except Exception:
            log.exception("call handler failed")
        finally:
            await call.hangup()
