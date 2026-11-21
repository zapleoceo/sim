"""How she types right now (docs/writing.md): the device in her hands and how much attention she
has. The owner (2026-11-22): «стили общения зависят от обстоятельств: со смартфона или с ноута,
голосом или видео, на бегу или в лифте». Pure values; chosen by sim/services/writing.py."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Device(StrEnum):
    PHONE = "phone"
    LAPTOP = "laptop"


class Pace(StrEnum):
    RELAXED = "relaxed"     # free, phone or laptop in hand
    BUSY = "busy"           # doing something or on a call: one line between things
    MOVING = "moving"       # walking, in a tram, in a lift: one hand, a few words
    SLEEPY = "sleepy"       # late night, in bed


@dataclass(frozen=True)
class WritingMode:
    device: Device
    pace: Pace
    situation: str          # Russian, for the context block: «едет в трамвае»
    max_messages: int       # messages in one turn
    max_chars: int          # a soft limit for one message
    typo_rate: float        # chance that one message gets one smartphone typo
    lowercase_rate: float   # chance that a message starts with a small letter


# (device, pace) -> (max_messages, max_chars, typo_rate, lowercase_rate)
LIMITS: dict[tuple[Device, Pace], tuple[int, int, float, float]] = {
    (Device.PHONE, Pace.RELAXED): (3, 220, 0.12, 0.3),
    (Device.PHONE, Pace.BUSY): (1, 90, 0.25, 0.6),
    (Device.PHONE, Pace.MOVING): (1, 60, 0.35, 0.7),
    (Device.PHONE, Pace.SLEEPY): (2, 90, 0.25, 0.8),
    (Device.LAPTOP, Pace.RELAXED): (3, 400, 0.03, 0.0),
    (Device.LAPTOP, Pace.BUSY): (1, 140, 0.06, 0.1),
    (Device.LAPTOP, Pace.MOVING): (1, 60, 0.35, 0.7),
    (Device.LAPTOP, Pace.SLEEPY): (2, 120, 0.1, 0.4),
}


def mode(device: Device, pace: Pace, situation: str) -> WritingMode:
    messages, chars, typos, lower = LIMITS[(device, pace)]
    return WritingMode(device=device, pace=pace, situation=situation, max_messages=messages,
                       max_chars=chars, typo_rate=typos, lowercase_rate=lower)


DEFAULT_MODE = mode(Device.PHONE, Pace.RELAXED, "свободна, телефон в руке")
