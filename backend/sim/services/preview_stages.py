"""Read-only stage diagnostics for the persona test chat, never a live reply job."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sim.services.app_settings import AppSettings
from sim.services.reply_schedule import Incoming, plan_reply, reason_text, rules_from


@dataclass(frozen=True)
class PreviewStages:
    schedule: bool = False
    debounce: bool = False
    decision_context: bool = True


def stage_diagnostics(  # noqa: PLR0913
    stages: PreviewStages, cfg: AppSettings, now: datetime, routine: str, timezone: str, text: str,
) -> dict[str, object]:
    """Estimate when a fresh message's job would start, without sleeping or creating a job."""
    plan = (plan_reply(now, routine, timezone, [Incoming(text, now)], rules_from(cfg))
            if stages.schedule else None)
    schedule_seconds = max(0, int((plan.at - now).total_seconds())) if plan else 0
    debounce_seconds = cfg.reply_debounce_s if stages.debounce else 0
    return {
        "schedule": {
            "enabled": stages.schedule,
            "wait_s": schedule_seconds,
            "reason": reason_text(plan.delay, timezone) if plan and plan.delay else "",
        },
        "debounce": {"enabled": stages.debounce, "wait_s": debounce_seconds},
        "estimated_wait_s": max(schedule_seconds, debounce_seconds),
        "decision_context": stages.decision_context,
    }
