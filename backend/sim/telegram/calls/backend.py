"""The call transport as the call service sees it: an incoming call that can be declined,
answered, listened to and spoken into. py-tgcalls implements it (`pytgcalls_backend`); tests use
a fake, so the call logic runs without the native library."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from sim.ports import Peer


class CallGone(Exception):
    """The caller hung up before the call was picked up."""


class IncomingCall(Protocol):
    external_id: str        # Telegram user id of the caller (a private call has no own id here)
    rate_hz: int            # PCM rate of `frames()` and `play()` (mono s16le)

    @property
    def ended(self) -> bool: ...

    async def peer(self) -> Peer: ...

    async def decline(self) -> None: ...

    async def answer(self) -> None:
        """Picks up, or raises CallGone when the caller is no longer there."""
        ...

    def frames(self) -> AsyncIterator[bytes]:
        """The caller's audio until the call ends."""
        ...

    async def play(self, pcm: bytes) -> None:
        """One frame of the persona's audio."""
        ...

    async def hangup(self) -> None: ...


CallHandler = Callable[[IncomingCall], Awaitable[None]]


class CallBackend(Protocol):
    async def start(self, on_call: CallHandler) -> None: ...
