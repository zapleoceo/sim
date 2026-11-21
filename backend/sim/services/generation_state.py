"""One definition of «a generation still in progress» (docs/reply.md, «Генерация: сроки»).

A generation is in progress while it is `queued` or `running` AND its last change of status
(`updated_at`: created for `queued`, claimed by the media worker for `running`) is within
`GENERATION_FRESH`. The reply context («не обещай ещё раз, отправится само»,
sim/services/context/builder.py) and the persona status («Делает фото»,
sim/services/persona_status.py) both read `active_generations`, so neither promises a picture
for ever. The reaper (sim/services/reaper.py) fails a `running` generation older than its
`run_budget` and a `queued` one that never started within `GENERATION_FRESH`: a row the window
hides is soon a failed one, with its `generation_failed` event."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Generation
from sim.domain.enums import GenerationKind, GenerationStatus

# One Broker media job may poll this long (`SIM_BROKER_VIDEO_BUDGET_S`, 900 s by default; the
# image model uses the same budget, sim/adapters/broker/image.py).
MEDIA_JOB = timedelta(seconds=900)
SLACK = timedelta(minutes=5)            # storing, cutting a circle, a slow database
# How long a claimed generation may run: a photo is one image job; a video or a circle is the
# photo first and then the clip.
RUN_BUDGET: dict[GenerationKind, timedelta] = {
    GenerationKind.PHOTO: MEDIA_JOB + SLACK,
    GenerationKind.VOICE: MEDIA_JOB + SLACK,
    GenerationKind.VIDEO: 2 * MEDIA_JOB + SLACK,
    GenerationKind.VIDEO_NOTE: 2 * MEDIA_JOB + SLACK,
}
# Longer than every run budget: the reaper fails a stuck row before the window hides it.
GENERATION_FRESH = max(RUN_BUDGET.values()) + SLACK
IN_PROGRESS = (GenerationStatus.QUEUED, GenerationStatus.RUNNING)


def run_budget(kind: GenerationKind) -> timedelta:
    return RUN_BUDGET.get(kind, max(RUN_BUDGET.values()))


async def active_generations(session: AsyncSession, chat_ids: Iterable[int],
                             now: datetime) -> dict[int, list[Generation]]:
    """The generations still in progress per chat, oldest first."""
    ids = list(chat_ids)
    if not ids:
        return {}
    rows = await session.scalars(
        select(Generation)
        .where(Generation.chat_id.in_(ids), Generation.status.in_(IN_PROGRESS),
               Generation.updated_at >= now - GENERATION_FRESH)
        .order_by(Generation.chat_id, Generation.id))
    active: dict[int, list[Generation]] = {}
    for generation in rows:
        if generation.chat_id is not None:
            active.setdefault(generation.chat_id, []).append(generation)
    return active
