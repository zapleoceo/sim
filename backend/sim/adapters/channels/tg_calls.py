"""Private Telegram voice calls of a userbot account, over py-tgcalls / ntgcalls.

Telethon carries no VoIP; py-tgcalls attaches to the SAME Telethon client (one session must not
be connected twice) and drives ntgcalls, which speaks Telegram's call protocol. Audio is raw
PCM both ways (`ExternalMedia.AUDIO` out via `send_frame`, `RecordStream` in via a bounded
native frame handoff), so the conversation itself happens in sim.services.calls, not here.

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
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from threading import Lock
from types import SimpleNamespace
from typing import Any

from sim.adapters.channels.capabilities import CallAnswerTimeout, CallGone
from sim.adapters.pcm import to_stereo

__all__ = ["ANSWER_TIMEOUT_S", "CallAnswerTimeout", "CallGone", "TelegramCall",
           "TelegramCallBridge", "has_status", "load_pytgcalls"]

log = logging.getLogger(__name__)

CALL_RATE_HZ = 48_000           # Telegram calls run at 48 kHz; ntgcalls frames are 10 ms
ANSWER_TIMEOUT_S = 20           # the caller's confirmation of a picked-up call (CallConfig.timeout)
# Her voice goes out as STEREO, as in py-tgcalls' own frame-sending example: external mono frames
# were accepted by send_frame and never heard on a live call. The caller's side stays mono.
OUT_CHANNELS = 2
IN_CHANNELS = 1
MAX_INPUT_FRAMES = 50           # 500 ms at the usual 10 ms Telegram framing
MAX_INPUT_FRAME_BYTES = 4 * 1024
MAX_NATIVE_PENDING_FRAMES = 128
_ENDED = object()
TIMED_OUT_ANSWER = "TimedOutAnswer"    # py-tgcalls' exception when the caller never confirmed


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
        StreamFrames=StreamFrames, AudioParameters=AudioParameters, use_native_mic_mailbox=True,
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
        # One reserved slot guarantees hang-up can wake the reader after saturation.
        self.inbox: asyncio.Queue[Any] = asyncio.Queue(maxsize=MAX_INPUT_FRAMES + 1)
        self.over = asyncio.Event()     # the call ended, whoever ended it
        self.answered = False           # we started picking up: the call is ours to hang up

    @property
    def ended(self) -> bool:
        return self.over.is_set()

    async def answer(self) -> None:
        """Picks up, or raises: CallGone when the caller is no longer there, CallAnswerTimeout
        (py-tgcalls' TimedOutAnswer) when the caller's side did not confirm in
        `answer_timeout_s`."""
        if self.ended:
            raise CallGone("the caller hung up before the call was picked up")
        self.answered = True
        accept = asyncio.ensure_future(self.accept())
        gone = asyncio.ensure_future(self.over.wait())
        try:
            await asyncio.wait({accept, gone}, return_when=asyncio.FIRST_COMPLETED)
            picked_up = accept.done()
            if picked_up:
                try:
                    await accept
                except Exception as exc:
                    if type(exc).__name__ == TIMED_OUT_ANSWER:
                        raise CallAnswerTimeout(
                            f"the caller's side did not confirm in {self.bridge.answer_timeout_s} s"
                        ) from exc
                    raise
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
        out = lib.AudioParameters(self.rate_hz, OUT_CHANNELS)
        await calls.play(self.chat_id, lib.MediaStream(lib.ExternalMedia.AUDIO, out),
                         lib.CallConfig(timeout=self.bridge.answer_timeout_s))
        await calls.record(self.chat_id,
                           lib.RecordStream(True, lib.AudioParameters(self.rate_hz, IN_CHANNELS)))

    def feed(self, pcm: bytes) -> None:
        if self.ended or not pcm or len(pcm) > MAX_INPUT_FRAME_BYTES:
            return
        if self.inbox.qsize() >= MAX_INPUT_FRAMES:
            self.inbox.get_nowait()  # stale speech is less useful than the newest audio
        self.inbox.put_nowait(bytes(pcm))

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
        """One 10 ms mono frame of her voice, sent as the stereo frame the stream expects."""
        if not self.ended:
            await self.bridge.calls.send_frame(self.chat_id, self.bridge.lib.Device.MICROPHONE,
                                               to_stereo(pcm))

    async def hangup(self) -> None:
        """Ends an answered call. A call that was never answered is only forgotten: it keeps
        ringing on the account's other devices, so a person can still pick it up."""
        already = self.ended
        self.remote_ended()
        self.bridge.forget(self)
        if self.answered and not already:
            with contextlib.suppress(Exception):
                await self.bridge.calls.leave_call(self.chat_id)


class NativePcmMailbox:
    """Bounded native-thread handoff with at most one pending drain per bridge."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.lock = Lock()
        self.targets: dict[int, TelegramCall] = {}
        self.pending: deque[tuple[TelegramCall, bytes]] = deque(maxlen=MAX_NATIVE_PENDING_FRAMES)
        self.scheduled = False
        self.closed = False

    def watch(self, call: TelegramCall) -> None:
        with self.lock:
            self.targets[call.chat_id] = call

    def forget(self, call: TelegramCall) -> None:
        with self.lock:
            if self.targets.get(call.chat_id) is call:
                del self.targets[call.chat_id]
            self.pending = deque((item for item in self.pending if item[0] is not call),
                                 maxlen=MAX_NATIVE_PENDING_FRAMES)

    def offer(self, chat_id: int, frames: list[Any]) -> None:
        with self.lock:
            call = self.targets.get(chat_id)
            if self.closed or call is None:
                return
            for frame in frames[-MAX_NATIVE_PENDING_FRAMES:]:
                pcm = frame.data
                if pcm and len(pcm) <= MAX_INPUT_FRAME_BYTES:
                    self.pending.append((call, bytes(pcm)))
            if not self.pending or self.scheduled:
                return
            self.scheduled = True
        try:
            self.loop.call_soon_threadsafe(self.drain)
        except RuntimeError:  # owner loop already closed; release all references
            with self.lock:
                self.closed = True
                self.scheduled = False
                self.pending.clear()
                self.targets.clear()

    def drain(self) -> None:
        with self.lock:
            batch = self.pending
            self.pending = deque(maxlen=MAX_NATIVE_PENDING_FRAMES)
            self.scheduled = False
        for call, pcm in batch:
            call.feed(pcm)


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
        self.use_native_mic_mailbox = getattr(self.lib, "use_native_mic_mailbox", False)
        self.native_pcm: NativePcmMailbox | None = None

    async def start(self) -> None:
        if self.started:
            return
        # Neither partial native startup nor a failed mailbox install may dispatch a call.
        await self.calls.start()
        if self.use_native_mic_mailbox:
            self.install_native_mic_mailbox()
        self.calls.on_update()(self.on_update)
        self.started = True

    def install_native_mic_mailbox(self) -> None:
        lib = self.lib
        mailbox = NativePcmMailbox(asyncio.get_running_loop())

        def on_frames(chat_id: int, mode: Any, device: Any, frames: list[Any]) -> None:
            if (lib.Direction.from_raw(mode) == lib.Direction.INCOMING
                    and lib.Device.from_raw(device) == lib.Device.MICROPHONE):
                mailbox.offer(int(chat_id), frames)

        # Locked PyTgCalls 3.0.0 + NTgCalls 3.0.7: this setter replaces the single
        # per-batch run_coroutine_threadsafe callback. All non-frame hooks stay intact.
        self.calls._binding.on_frames(on_frames)
        self.native_pcm = mailbox

    def forget(self, call: TelegramCall) -> None:
        """Only its own entry: the same person may already be ringing again, and that newer
        call must keep receiving its frames and its hang-up."""
        if self.active.get(call.chat_id) is call:
            del self.active[call.chat_id]
        if self.native_pcm is not None:
            self.native_pcm.forget(call)

    async def on_update(self, _client: Any, update: Any) -> None:
        lib = self.lib
        if self.use_native_mic_mailbox and not self.started:
            return
        if isinstance(update, lib.StreamFrames):
            if self.use_native_mic_mailbox:
                return
            call = self.active.get(int(update.chat_id))
            incoming = (update.direction == lib.Direction.INCOMING
                        and update.device == lib.Device.MICROPHONE)
            if call is not None and incoming:
                for frame in update.frames:
                    call.feed(frame.frame)
            return
        if not isinstance(update, lib.ChatUpdate):
            return
        chat_id, status, statuses = int(update.chat_id), update.status, lib.ChatUpdate.Status
        if has_status(status, statuses.INCOMING_CALL):
            if chat_id in self.active:
                return
            call = TelegramCall(self, chat_id)
            self.active[chat_id] = call
            if self.native_pcm is not None:
                self.native_pcm.watch(call)
            task = asyncio.ensure_future(self.run(call))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        elif any(has_status(status, getattr(statuses, name, 0))
                 for name in ("DISCARDED_CALL", "LEFT_CALL", "BUSY_CALL")):
            call = self.active.get(chat_id)
            if call is not None:
                call.remote_ended()
                self.forget(call)

    async def run(self, call: TelegramCall) -> None:
        try:
            await self.on_call(call)
        except Exception:
            log.exception("call handler failed")
        finally:
            await call.hangup()
