"""Live voice calls of the Telegram accounts (docs/calls.md).

`CallsAttacher` is a runtime hook: on every fresh Telethon connection it puts a call backend
(py-tgcalls) on the same client and routes each incoming call to `CallService.handle`. A backend
that cannot start (no native library, an account that cannot call) only disables calls for that
account: messaging keeps working. `dial` places her own call through the backend of the chat's
account (docs/initiative.md)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sim.telegram.calls.backend import CallBackend, IncomingCall
from sim.telegram.calls.service import CallService

log = logging.getLogger(__name__)

BackendFactory = Callable[[Any], CallBackend]


def default_backend(client: Any) -> CallBackend:
    from sim.telegram.calls.pytgcalls_backend import PyTgCallsBackend

    return PyTgCallsBackend(client)


class CallsAttacher:
    def __init__(self, service: CallService,
                 backend_factory: BackendFactory = default_backend) -> None:
        self.service = service
        self.factory = backend_factory
        self.backends: dict[int, CallBackend] = {}

    async def attach(self, channel_id: int, client: Any) -> None:
        async def on_call(call: IncomingCall) -> None:
            await self.service.handle(channel_id, call)

        try:
            backend = self.factory(client)
            await backend.start(on_call)
        except Exception as exc:
            log.warning("telegram channel %s: calls disabled: %s: %s", channel_id,
                        type(exc).__name__, str(exc)[:200])
            self.backends.pop(channel_id, None)
            return
        self.backends[channel_id] = backend

    async def dial(self, contact_id: int) -> None:
        await self.service.dial(contact_id, self.backends)
