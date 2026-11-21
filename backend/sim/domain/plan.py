"""The persona's answer: a strict JSON plan the reply model must return (docs/reply.md).

The JSON schema sent to the Broker (`response_format: json_schema`) is generated from these
models, so the prompt, the validation and the executor share one definition."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateSpec(_Strict):
    """Ask for a new photo / video of the persona, made from her reference photos."""

    prompt: str = Field(min_length=3, max_length=2000,
                        description="What the picture shows: scene, pose, clothes, light, mood.")
    caption: str = Field(min_length=3, max_length=300,
                         description="One line: what it is, saved to the media library.")
    tags: list[str] = Field(default_factory=list, max_length=12,
                            description="Short lowercase tags so it can be reused later.")


class TextAction(_Strict):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=4000)


class VoiceAction(_Strict):
    type: Literal["voice"]
    text: str = Field(min_length=1, max_length=1500, description="What she says out loud.")


class PhotoAction(_Strict):
    """Send a ready photo (`media_id` from the catalog) or generate a new one (`generate`)."""

    type: Literal["photo"]
    media_id: int | None = None
    generate: GenerateSpec | None = None
    caption: str = Field(default="", max_length=1000, description="Text under the photo.")


class VideoAction(_Strict):
    type: Literal["video"]
    media_id: int | None = None
    generate: GenerateSpec | None = None
    caption: str = Field(default="", max_length=1000)


Action = Annotated[TextAction | VoiceAction | PhotoAction | VideoAction,
                   Field(discriminator="type")]


class ClientFact(_Strict):
    fact: str = Field(min_length=3, max_length=300)


class ReplyPlan(_Strict):
    """`messages` empty = stay silent this turn (allowed, e.g. the client said goodbye)."""

    messages: list[Action] = Field(default_factory=list, max_length=8)
    # New durable facts about the client learned from the latest messages (name, city, ...).
    remember: list[ClientFact] = Field(default_factory=list, max_length=10)


def media_choice_errors(plan: ReplyPlan) -> list[str]:
    """A photo/video action needs exactly one of `media_id` / `generate`."""
    errors = []
    for i, action in enumerate(plan.messages):
        if isinstance(action, PhotoAction | VideoAction) and (
            (action.media_id is None) == (action.generate is None)
        ):
            errors.append(f"messages[{i}]: give exactly one of media_id or generate")
    return errors


class GuardVerdict(_Strict):
    ok: bool
    reason: str = Field(default="", max_length=500)
