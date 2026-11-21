"""What the persona may do this turn, from her switches and her agents' models. The same
flags are shown to the reply model (facts) and enforced on its plan (plan_check)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from sim.db.models import Persona
from sim.domain.agents import AgentKey, agent_config
from sim.domain.scene import CameraHold, SceneCard
from sim.services.visual import camera_for


@dataclass(frozen=True)
class Capabilities:
    voice: bool             # voice notes (TTS)
    photo: bool             # send photos from the catalog
    photo_generate: bool    # make new photos by references
    video: bool             # send videos from the catalog
    video_generate: bool    # make new videos (photo by references -> video)
    # Why she can not film a video note right now (driving, running…), "" = she can.
    camera_block: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capabilities_of(persona: Persona) -> Capabilities:
    photo = bool(persona.photo_enabled)
    video = bool(persona.video_enabled)
    tts, image, clip = (agent_config(persona, key).model
                        for key in (AgentKey.TTS, AgentKey.IMAGE, AgentKey.VIDEO))
    return Capabilities(
        voice=bool(persona.voice_enabled and persona.voice_id and tts),
        photo=photo,
        photo_generate=photo and bool(image),
        video=video,
        video_generate=video and bool(clip and image),
    )


def with_camera(caps: Capabilities, scene: SceneCard | None) -> Capabilities:
    """Add «she can not film a video note now» (driving, running, asleep…) from her scene."""
    camera = camera_for(scene)
    if camera.hold == CameraHold.IMPOSSIBLE:
        return replace(caps, camera_block=camera.reason)
    return caps
