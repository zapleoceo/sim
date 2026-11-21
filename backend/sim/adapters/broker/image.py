"""ImageGenPort: a photo from a prompt and appearance references.

Main route: `POST /v1/images/jobs` (Gemini image models, several references), then the job poll.
Fallback for `codex-sub/*` models: the subscription route of v1 — `POST /v1/jobs` with the
Codex image tool, references as image blocks, the PNG in the finished job's `files[]`.

The `/v1/images/jobs` contract is being added to the Broker now: its request is built only in
`images_job_body` and its answer read only in `image_of`, so a change touches those two."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sim.adapters.broker.blocks import attachment_block
from sim.adapters.broker.http import BrokerHttp, decode_b64, encode_b64, usage_of
from sim.adapters.broker.tier import with_tier
from sim.ports import AUTO_TIER, MediaResult, PortError

IMAGES_PATH = "/v1/images/jobs"
JOBS_PATH = "/v1/jobs"
CODEX_PREFIX = "codex-sub/"
CODEX_CAPABILITY = "chat:fast"      # any chat lane carries a subscription pin; v1 used this one
MAX_CODEX_REFERENCES = 5
DEFAULT_ASPECT_RATIO = "3:4"        # a phone photo
DEFAULT_WORKFLOW = "sim-photo"


def images_job_body(*, prompt: str, references: Sequence[tuple[bytes, str]], model: str,
                    aspect_ratio: str, workflow: str,
                    key_tier: str = AUTO_TIER) -> dict[str, Any]:
    body: dict[str, Any] = {
        "prompt": prompt,
        "references": [{"image_base64": encode_b64(data), "mime": mime}
                       for data, mime in references],
        "aspect_ratio": aspect_ratio,
        "workflow": workflow,
    }
    if model:
        body["model"] = model
    return with_tier(body, key_tier, model)


def codex_prompt(prompt: str) -> str:
    """The wording the Broker documents for the Codex image tool (subscription-models.md)."""
    return (f"$imagegen Generate exactly one real PNG image.\n\n{prompt}\n\n"
            "Use the built-in GPT image generation tool; do not draw it with code or SVG.")


def codex_image_body(*, prompt: str, references: Sequence[tuple[bytes, str]],
                     model: str) -> dict[str, Any]:
    """No max_tokens (subscription contract) and no `no_shell` (it switches the image tool off)."""
    content: list[dict[str, Any]] = [{"type": "text", "text": codex_prompt(prompt)}]
    content += [attachment_block(data, mime) for data, mime in references[:MAX_CODEX_REFERENCES]]
    return {"model": model, "messages": [{"role": "user", "content": content}]}


def _png_from_files(files: Any) -> str | None:
    for entry in files if isinstance(files, list) else []:
        if (isinstance(entry, Mapping) and str(entry.get("path", "")).lower().endswith(".png")
                and entry.get("content_b64")):
            return str(entry["content_b64"])
    return None


def image_of(data: Mapping[str, Any]) -> tuple[bytes, str]:
    """The picture of a finished job: `image_base64` + `mime_type` (images jobs), the first of
    `images[]`, or the first PNG in `files[]` (Codex)."""
    if data.get("image_base64"):
        return (decode_b64(data["image_base64"], what="image"),
                str(data.get("mime_type") or "image/png"))
    images = data.get("images")
    if isinstance(images, list) and images and isinstance(images[0], Mapping):
        first = images[0]
        payload = first.get("data") or first.get("image_base64") or first.get("b64")
        return (decode_b64(payload, what="image"),
                str(first.get("mime") or first.get("mime_type") or "image/png"))
    png = _png_from_files(data.get("files"))
    if png is not None:
        return decode_b64(png, what="image"), "image/png"
    raise PortError("image: the job finished without a picture")


class BrokerImageGen:
    def __init__(self, http: BrokerHttp, *, aspect_ratio: str = DEFAULT_ASPECT_RATIO,
                 workflow: str = DEFAULT_WORKFLOW) -> None:
        self.http = http
        self.aspect_ratio = aspect_ratio
        self.workflow = workflow

    async def generate_image(self, *, prompt: str, references: Sequence[tuple[bytes, str]],
                             model: str, key_tier: str = AUTO_TIER) -> MediaResult:
        """`key_tier` goes with the images job; the Codex route runs on the subscription."""
        if not prompt.strip():
            raise PortError("image: empty prompt")
        if model.startswith(CODEX_PREFIX):
            body = codex_image_body(prompt=prompt, references=references, model=model)
            params = {"capability": CODEX_CAPABILITY, "workflow": self.workflow}
            body["workflow"] = self.workflow
            outcome = await self.http.submit(JOBS_PATH, body, what="image",
                                             budget_s=self.http.media_budget_s, params=params)
        else:
            body = images_job_body(prompt=prompt, references=references, model=model,
                                   aspect_ratio=self.aspect_ratio, workflow=self.workflow,
                                   key_tier=key_tier)
            outcome = await self.http.submit(IMAGES_PATH, body, what="image",
                                             budget_s=self.http.media_budget_s)
        image, mime = image_of(outcome.data)
        return MediaResult(data=image, mime=mime,
                           usage=usage_of(outcome.data, model=model,
                                          latency_ms=outcome.latency_ms, job_id=outcome.job_id))
