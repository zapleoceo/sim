"""The course of one phone call, kept two ways:

- `step(...)`: what the admin reads. Every action that shapes how and when she answers (the
  pause before picking up, opening the voice session and its retries, picking up, each review
  of a reply and its verdict, interruptions, how the call ended) becomes one plain-language line
  with its time since the ring. The call service writes these lines to the dialog's journal as
  one «Ход звонка» record (`sim.services.journal.CALL_STEPS`), for every call it handled, so
  nothing that formed her answer is hidden in a server log. Always on.
- `event(...)`: the same key moments for the worker log (logger `sim.calls.trace`), off by
  default (`SIM_CALL_DIAGNOSTICS`). A step is also logged as an event.

Counters of what moved (frames, bytes, pauses) are always kept: a handful of integer additions
per frame; the call service reads them to warn about a call in which the caller never heard her,
and their totals close the «Ход звонка» record."""
from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable

log = logging.getLogger("sim.calls.trace")

MAX_STEPS = 120          # a long call with many replies still fits one journal record
COUNTER_LABELS = {
    "frames_in": "кадров от клиента",
    "frames_out": "кадров её речи отправлено",
    "model_audio_bytes": "байт речи от модели",
    "model_turns": "реплик модели",
    "interruptions": "перебиваний",
    "pacer_restarts": "сбоев темпа воспроизведения",
}


def seconds(value: float) -> str:
    return f"{value:.1f} с".replace(".", ",")


class CallTrace:
    def __init__(self, enabled: bool, channel_id: int,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.enabled = enabled
        self.channel_id = channel_id
        self.mono = monotonic
        self.started = monotonic()
        self.seen: set[str] = set()
        self.counters: Counter[str] = Counter()
        self.steps: list[str] = []
        self.dropped = 0

    def elapsed(self) -> float:
        return self.mono() - self.started

    def event(self, name: str, **fields: object) -> None:
        """One key moment in the worker log, e.g. `call ch=3 +1840ms answered`."""
        if not self.enabled:
            return
        details = "".join(f" {key}={value}" for key, value in fields.items())
        log.info("call ch=%s +%dms %s%s", self.channel_id, round(self.elapsed() * 1000),
                 name, details)

    def step(self, name: str, text: str, **fields: object) -> None:
        """One action of the call as the admin reads it in the journal (and as a log event)."""
        self.event(name, **fields)
        if len(self.steps) >= MAX_STEPS:
            self.dropped += 1
            return
        self.steps.append(f"+{seconds(self.elapsed())} · {text}")

    def first(self, name: str, text: str = "", **fields: object) -> None:
        """A moment that matters only the first time (first frame, first model audio)."""
        if name not in self.seen:
            self.seen.add(name)
            if text:
                self.step(name, text, **fields)
            else:
                self.event(name, **fields)

    def count(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def summary(self) -> None:
        self.event("summary", **dict(sorted(self.counters.items())))

    def report(self) -> str:
        """The «Ход звонка» text: one step per line, then the totals of what moved."""
        lines = list(self.steps)
        if self.dropped:
            lines.append(f"… ещё {self.dropped} шагов не показано")
        totals = [f"{label}: {self.counters[key]}" for key, label in COUNTER_LABELS.items()
                  if self.counters[key]]
        if totals:
            lines.append("Итого: " + ", ".join(totals))
        return "\n".join(lines)
