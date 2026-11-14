"""Image port: prompt + reference photos → one picture. The only implementation is the AIbroker
client (`POST /v1/jobs` with a subscription model that has the image generation tool) — no
provider keys in Sim."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImageReference:
    """A photo the picture must follow (how the person looks)."""
    data: bytes
    mime: str


@dataclass(frozen=True)
class ImageResult:
    image: bytes                      # PNG, as the broker returned it
    model: str | None = None
    cost_usd: float | None = None     # None on a subscription model: nothing is billed per call


class ImageError(Exception):
    """The broker refused or failed the generation (scope not granted, model not allowed or not
    verified for image input, quota, provider error) or the job finished without a picture."""


class ImagePort(Protocol):
    async def generate_image(
        self, prompt: str, *, model: str, references: Sequence[ImageReference] = (),
        workflow: str | None = None,
    ) -> ImageResult: ...
