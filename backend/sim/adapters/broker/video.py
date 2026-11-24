"""VideoGenPort over `POST /v1/video/jobs` (Google or HeyGen), then the job poll: a vertical clip
with the first frame taken from a generated photo (two-step generation, docs/spec-v2.md §10a)."""
from __future__ import annotations

from typing import Any

from sim.adapters.broker.http import BrokerHttp, decode_b64, encode_b64, usage_of
from sim.adapters.broker.tier import with_tier
from sim.domain.agents import HEYGEN_VIDEO_MODEL
from sim.ports import AUTO_TIER, MediaResult, PortError

VIDEO_PATH = "/v1/video/jobs"
FIRST_FRAME_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_FIRST_FRAME_BYTES = 4 * 1024 * 1024
MIN_DURATION_S, MAX_DURATION_S = 3, 15
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_RESOLUTION = "720p"
DEFAULT_WORKFLOW = "sim-video"


def video_job_body(*, prompt: str, first_frame: tuple[bytes, str] | None, model: str,
                   duration_s: int, aspect_ratio: str, resolution: str,
                   workflow: str, key_tier: str = AUTO_TIER) -> dict[str, Any]:
    if not prompt.strip():
        raise PortError("video: empty prompt")
    body: dict[str, Any] = {
        "prompt": prompt,
        "duration_s": max(MIN_DURATION_S, min(int(duration_s), MAX_DURATION_S)),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "workflow": workflow,
    }
    if model:
        body["model"] = model
    if model == HEYGEN_VIDEO_MODEL:
        if first_frame is None:
            raise PortError("HeyGen video: first frame required")
        body.update(provider="heygen", resolution="768p",
                    duration_s=max(5, body["duration_s"]))
    if first_frame is not None:
        data, mime = first_frame
        kind = mime.lower()
        if kind not in FIRST_FRAME_MIMES:
            raise PortError(f"video: first frame must be JPEG, PNG or WebP, not {kind}")
        if len(data) > MAX_FIRST_FRAME_BYTES:
            raise PortError("video: first frame is larger than 4 MB")
        body["image_base64"] = encode_b64(data)
        body["image_mime"] = kind
    return with_tier(body, key_tier, model)


class BrokerVideoGen:
    def __init__(self, http: BrokerHttp, *, aspect_ratio: str = DEFAULT_ASPECT_RATIO,
                 resolution: str = DEFAULT_RESOLUTION, workflow: str = DEFAULT_WORKFLOW) -> None:
        self.http = http
        self.aspect_ratio = aspect_ratio
        self.resolution = resolution
        self.workflow = workflow

    async def generate_video(self, *, prompt: str, first_frame: tuple[bytes, str] | None,
                             model: str, duration_s: int = 5,
                             key_tier: str = AUTO_TIER) -> MediaResult:
        body = video_job_body(prompt=prompt, first_frame=first_frame, model=model,
                              duration_s=duration_s, aspect_ratio=self.aspect_ratio,
                              resolution=self.resolution, workflow=self.workflow,
                              key_tier=key_tier)
        outcome = await self.http.submit(VIDEO_PATH, body, what="video",
                                         budget_s=self.http.media_budget_s)
        clip = decode_b64(outcome.data.get("video_base64"), what="video")
        return MediaResult(data=clip, mime=str(outcome.data.get("mime_type") or "video/mp4"),
                           usage=usage_of(outcome.data, model=model,
                                          latency_ms=outcome.latency_ms, job_id=outcome.job_id))
