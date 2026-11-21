"""Reading the stored life JSON leniently: what the API saved is valid, but a row written by an
older version must never break a reply. Invalid parts are skipped with a warning."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from sim.db.models import Persona
from sim.domain.life import DayEvent, PersonaLife

log = logging.getLogger(__name__)


def life_of(persona: Persona) -> PersonaLife:
    try:
        return PersonaLife.model_validate(persona.life or {})
    except ValidationError as exc:
        log.warning("persona %s: unreadable life JSON (%s errors)", persona.id,
                    exc.error_count())
        return PersonaLife()


def day_events(raw: Iterable[Any] | None) -> list[DayEvent]:
    events = []
    for item in raw or []:
        try:
            events.append(DayEvent.model_validate(item))
        except ValidationError:
            log.warning("skipping an unreadable day event: %r", item)
    return events
