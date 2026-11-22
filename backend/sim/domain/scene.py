"""The scene she is in right now: one source of truth for every photo, video, video note and voice
note (docs/persona-life.md, «Сцена и непрерывность»). Computed by code from the day plan (or the
routine) and the clock, never invented per generation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from sim.domain.life import Ambience, BusyLevel


class Light(StrEnum):
    MORNING_DAYLIGHT = "morning_daylight"
    DAYLIGHT = "daylight"
    GOLDEN_HOUR = "golden_hour"
    EVENING_LAMP = "evening_lamp"
    NIGHT = "night"


class ShotType(StrEnum):
    FRONT_SELFIE = "front_selfie"       # her phone's front camera at arm's length
    MIRROR_SELFIE = "mirror_selfie"     # the phone is seen only in the mirror
    TAKEN_BY_OTHER = "taken_by_other"   # someone she is with took it


class CameraHold(StrEnum):
    """How a video note / speaking video is filmed at this moment (visual.camera_for)."""

    HANDHELD = "handheld"       # front camera in her hand at arm's length (the usual way)
    PROPPED = "propped"         # the phone leans on something: her hands are busy
    IMPOSSIBLE = "impossible"   # she can not film now (driving, running, shower, asleep)


@dataclass(frozen=True)
class Camera:
    hold: CameraHold
    reason: str                 # Russian, for the journal and the plan check


@dataclass(frozen=True)
class SceneCard:
    day: date
    event_id: str               # stable within one event: same id = same room, same clothes
    place: str
    place_details: str
    activity: str
    with_whom: tuple[str, ...]
    outfit: str
    light: Light
    ambience: Ambience
    busy: BusyLevel
    indoors: bool

    def as_json(self) -> dict[str, Any]:
        """What a generation row keeps (`generation.scene`) to find same-scene photos later;
        the activity and the light are shown with the picture in the media library."""
        return {"day": self.day.isoformat(), "event_id": self.event_id, "place": self.place,
                "outfit": self.outfit, "ambience": self.ambience.value,
                "activity": self.activity, "light": self.light.value}
