"""How fast she speaks (`persona.voice_speed`, docs/calls.md and docs/persona-life.md): voice
notes and calls are stretched by ffmpeg `atempo`, which keeps the pitch. 1.0 = the model's own
pace; the persona form offers 0.8–1.5."""
from __future__ import annotations

MIN_SPEED, MAX_SPEED = 0.8, 1.5
NORMAL_SPEED = 1.0


def clamp_speed(speed: float | None) -> float:
    """The speed to use: within the form's range; empty or zero = the model's pace."""
    value = float(speed or NORMAL_SPEED)
    return round(min(MAX_SPEED, max(MIN_SPEED, value)), 2)


def is_normal(speed: float) -> bool:
    return abs(clamp_speed(speed) - NORMAL_SPEED) < 0.01
