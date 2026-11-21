"""Periodic `agent.reap_stuck`, every two minutes: rows a failure left half-done after their
commit — pending messages and queued generations without a job, generations and reply runs
whose worker died (sim/services/reaper.py, docs/deploy.md «Застрявшие строки»)."""
from __future__ import annotations

from sim.services.deps import get_deps
from sim.services.reaper import Reaper


async def reap_tick(timestamp: int) -> None:
    await Reaper(get_deps()).run()
