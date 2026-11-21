"""What the persona may do this turn, from her switches and the models chosen for her. The same
flags are shown to the reply model (facts) and enforced on its plan (plan_check)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sim.db.models import Persona


@dataclass(frozen=True)
class Capabilities:
    voice: bool             # voice notes (TTS)
    photo: bool             # send photos from the catalog
    photo_generate: bool    # make new photos by references
    video: bool             # send videos from the catalog
    video_generate: bool    # make new videos (photo by references -> video)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capabilities_of(persona: Persona) -> Capabilities:
    photo = bool(persona.photo_enabled)
    video = bool(persona.video_enabled)
    return Capabilities(
        voice=bool(persona.voice_enabled and persona.voice_id and persona.model_tts),
        photo=photo,
        photo_generate=photo and bool(persona.model_image),
        video=video,
        video_generate=video and bool(persona.model_video and persona.model_image),
    )
