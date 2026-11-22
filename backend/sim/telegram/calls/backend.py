"""The call transport as the call service sees it: an incoming call that can be declined,
answered, listened to and spoken into, and an outgoing one that rings until the callee picks up.
py-tgcalls implements it (`pytgcalls_backend`); tests use a fake, so the call logic runs without
the native library."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from sim.ports import Peer


class CallGone(Exception):
    """The caller hung up before the call was picked up."""


class NoAnswer(Exception):
    """The callee of her call did not pick up: `reason` is `no_answer` (it rang out),
    `declined` or `busy`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CallAudio(Protocol):
    """What a connected call carries, whoever called."""

    external_id: str        # Telegram user id of the other side (a private call has no own id)
    rate_hz: int            # PCM rate of `frames()` and `play()` (mono s16le)

    @property
    def ended(self) -> bool: ...

    def frames(self) -> AsyncIterator[bytes]:
        """The other side's audio until the call ends."""
        ...

    async def play(self, pcm: bytes) -> None:
        """One frame of the persona's audio."""
        ...

    async def hangup(self) -> None: ...


class IncomingCall(CallAudio, Protocol):
    async def peer(self) -> Peer: ...

    async def decline(self) -> None: ...

    async def answer(self) -> None:
        """Picks up, or raises CallGone when the caller is no longer there."""
        ...


class OutgoingCall(CallAudio, Protocol):
    async def ring(self, timeout_s: int) -> None:
        """Rings the callee until they pick up (then the audio flows), or raises NoAnswer."""
        ...


CallHandler = Callable[[IncomingCall], Awaitable[None]]


class CallBackend(Protocol):
    async def start(self, on_call: CallHandler) -> None: ...

    async def dial(self, peer: Peer) -> OutgoingCall:
        """Her call to a Telegram user; nothing rings before `OutgoingCall.ring`."""
        ...
