"""Video port: prompt (+ optional reference image) → short clip. The only implementation is the
AIbroker client (`POST /v1/video/jobs`, Google Gemini Omni on the Broker's paid Google account)
— no provider keys in Sim."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VideoResult:
    video: bytes                      # as the provider returned it (MP4 with its own audio track)
    mime_type: str
    duration_s: float | None = None
    model: str | None = None
    cost_usd: float | None = None


class VideoError(Exception):
    """The broker refused or failed the generation (scope not granted, no paid key, provider
    error, refused prompt, budget) or the clip could not be turned into a video note."""


class VideoPort(Protocol):
    async def generate_video(
        self, prompt: str, *, duration_s: int, resolution: str, aspect_ratio: str = "9:16",
        image: bytes | None = None, image_mime: str | None = None, workflow: str | None = None,
    ) -> VideoResult: ...
