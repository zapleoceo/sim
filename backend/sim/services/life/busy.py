"""When she answers, by how busy she is (docs/persona-life.md, «Занятость»). Code decides, not
the model:
- free (or nothing planned): the usual `persona.reply_delay_s`;
- partial: 3–15 minutes later, but not after the event ends;
- away (sleep, a run): after the event ends, plus 1–5 minutes.
The random part is a stable hash of a key (the chat and the message time), so a reply task that
re-checks the chat gets the same answer every time."""
from __future__ import annotations

import hashlib
from datetime import datetime

from sim.db.models import Persona
from sim.domain.life import BusyLevel
from sim.services.context.facts import to_local
from sim.services.life.agenda import Agenda

PARTIAL_DELAY_S = (180.0, 900.0)
AWAY_EXTRA_S = (60.0, 300.0)


def stable_fraction(key: str) -> float:
    """A number in [0, 1) that depends only on `key`."""
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def stable_seed(key: str, modulo: int = 1_000_000) -> int:
    return int(stable_fraction(key) * modulo) + 1


def _between(bounds: tuple[float, float], key: str) -> float:
    low, high = bounds
    return low + (high - low) * stable_fraction(key)


def reply_delay(now: datetime, day: Agenda, persona: Persona, *, key: str = "") -> float:
    """Seconds after `now` (when the client wrote) until she answers."""
    base = float(persona.reply_delay_s)
    local = to_local(now, persona.timezone)
    minute = day.minute_of(local)
    slot = day.at(minute)
    if slot is None or slot.busy == BusyLevel.FREE:
        return base
    seconds_left = (slot.end - minute) * 60.0 - local.second
    if slot.busy == BusyLevel.AWAY:
        return max(base, seconds_left + _between(AWAY_EXTRA_S, key))
    return max(base, min(_between(PARTIAL_DELAY_S, key), seconds_left))
