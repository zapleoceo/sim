"""Call diagnostics: the key moments of one phone call in the worker log, each with its time
since the ring, plus counters of what moved (frames, bytes, pauses).

The log lines are off by default (`SIM_CALL_DIAGNOSTICS`). The counters are always kept (a
handful of integer additions per frame), because the call service itself reads them to warn
about a call in which the caller never heard her."""
from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable

log = logging.getLogger("sim.calls.trace")


class CallTrace:
    def __init__(self, enabled: bool, channel_id: int,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.enabled = enabled
        self.channel_id = channel_id
        self.mono = monotonic
        self.started = monotonic()
        self.seen: set[str] = set()
        self.counters: Counter[str] = Counter()

    def event(self, name: str, **fields: object) -> None:
        """One key moment, e.g. `call ch=3 +1840ms answered`."""
        if not self.enabled:
            return
        details = "".join(f" {key}={value}" for key, value in fields.items())
        log.info("call ch=%s +%dms %s%s", self.channel_id,
                 round((self.mono() - self.started) * 1000), name, details)

    def first(self, name: str, **fields: object) -> None:
        """A moment that matters only the first time (first frame, first model audio)."""
        if name not in self.seen:
            self.seen.add(name)
            self.event(name, **fields)

    def count(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def summary(self) -> None:
        self.event("summary", **dict(sorted(self.counters.items())))
