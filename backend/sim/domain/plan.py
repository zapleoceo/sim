"""The persona's answer: a strict JSON plan the reply model must return (docs/reply.md).

The JSON schema sent to the Broker (`response_format: json_schema`) is generated from these
models, so the prompt, the validation and the executor share one definition."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateSpec(_Strict):
    """Ask for a new photo / video of the persona, made from her reference photos."""

    prompt: str = Field(min_length=3, max_length=2000,
                        description="Only her pose, action, expression and mood. The place, "
                                    "clothes and light come from her current scene.")
    shot: Literal["", "selfie", "mirror", "by_other"] = Field(
        default="", description="How the picture is taken: a front-camera selfie (default), a "
                                "mirror selfie, or taken by someone who is with her now.")
    caption: str = Field(min_length=3, max_length=300,
                         description="One line: what it is, saved to the media library.")
    tags: list[str] = Field(default_factory=list, max_length=12,
                            description="Short lowercase tags so it can be reused later.")
    speech: str = Field(default="", max_length=400,
                        description="Exact words she says on camera, in the chat language: 1–2 "
                                    "short spoken sentences addressed to this person, "
                                    "continuing the conversation.")


class TextAction(_Strict):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=4000)


class VoiceAction(_Strict):
    type: Literal["voice"]
    text: str = Field(min_length=1, max_length=1500, description="What she says out loud.")
    style: str = Field(default="", max_length=40,
                       description="Mood of THIS voice message in 1-3 words, e.g. «весело», "
                                   "«устало», «с улыбкой». Empty = her usual manner.")


def tts_style(base: str, mood: str) -> str:
    """The TTS delivery hint: the persona's usual manner plus this message's mood. Kept to a
    few words: the TTS model reads a long description aloud or pauses (seen in production)."""
    return ", ".join(part.strip() for part in (mood, base) if part.strip())[:80]


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


class VideoNoteAction(_Strict):
    """A round video message («кружок»): a ready video from the catalog (`media_id`), cut to a
    circle before sending, or a new one (`generate`). Telegram shows no text under a circle."""

    type: Literal["video_note"]
    media_id: int | None = None
    generate: GenerateSpec | None = None


MediaAction = PhotoAction | VideoAction | VideoNoteAction

Action = Annotated[TextAction | VoiceAction | PhotoAction | VideoAction | VideoNoteAction,
                   Field(discriminator="type")]


class ClientFact(_Strict):
    fact: str = Field(min_length=3, max_length=300)


class SelfFact(_Strict):
    fact: str = Field(min_length=3, max_length=300,
                      description="A new durable fact she invented about herself in this answer.")


class RecallSpec(_Strict):
    """«А помнишь…?» (docs/memory.md): look up past days before answering. Either dates — ISO
    or a Russian phrase («позавчера», «в прошлую субботу», «неделю назад») that code resolves
    against her clock — or a topic to search for."""

    date_from: str = Field(default="", max_length=60,
                           description="First day: ISO date or a phrase like «позавчера».")
    date_to: str = Field(default="", max_length=60,
                         description="Last day of a period; empty = the same as date_from.")
    query: str = Field(default="", max_length=200,
                       description="What to look for when the day is unknown, e.g. «работа».")

    @model_validator(mode="after")
    def _something(self) -> RecallSpec:
        if not self.date_from.strip() and not self.query.strip():
            raise ValueError("recall needs date_from or query")
        return self


class ReplyPlan(_Strict):
    """`messages` empty = stay silent this turn (allowed, e.g. the client said goodbye).
    `recall` with no messages = «let me remember first»: code looks the days up and asks again
    (once per turn); with messages it is ignored."""

    messages: list[Action] = Field(default_factory=list, max_length=8)
    # New durable facts about the client learned from the latest messages (name, city, ...).
    remember: list[ClientFact] = Field(default_factory=list, max_length=10)
    # New durable facts about HERSELF invented in this answer (the neighbour's dog is Rex):
    # kept in `persona_canon` and shown in every chat (docs/persona-life.md, «Канон»).
    canon: list[SelfFact] = Field(default_factory=list, max_length=5)
    recall: RecallSpec | None = None

    @property
    def wants_recall(self) -> bool:
        return self.recall is not None and not self.messages


def media_choice_errors(plan: ReplyPlan) -> list[str]:
    """A photo / video / video note action needs exactly one of `media_id` / `generate`."""
    errors = []
    for i, action in enumerate(plan.messages):
        if isinstance(action, MediaAction) and (
            (action.media_id is None) == (action.generate is None)
        ):
            errors.append(f"messages[{i}]: give exactly one of media_id or generate")
    return errors


def speech_errors(plan: ReplyPlan) -> list[str]:
    """A new video note is a spoken message: `generate.speech` must say what she says."""
    errors = []
    for i, action in enumerate(plan.messages):
        if (isinstance(action, VideoNoteAction) and action.generate is not None
                and not action.generate.speech.strip()):
            errors.append(f"messages[{i}]: у нового кружка нужен generate.speech — точные "
                          "слова, которые ты говоришь в камеру этому человеку")
    return errors


class GuardVerdict(_Strict):
    ok: bool
    reason: str = Field(default="", max_length=500)
