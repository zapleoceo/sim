"""What the persona may do this turn, from her switches, her agents' models and the messenger of
the chat (sim/domain/channels.py: Instagram has no voice notes, only Telegram has round videos).
The same flags are shown to the reply model (facts) and enforced on its plan (plan_check)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from sim.db.models import Persona
from sim.domain.agents import AgentKey, agent_config
from sim.domain.channels import spec_of
from sim.domain.enums import ChannelKind, MessageKind
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
    # The chat's messenger shows a round video message («кружок»); only Telegram does.
    video_note: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capabilities_of(persona: Persona,
                    channel: ChannelKind = ChannelKind.TELEGRAM_USER) -> Capabilities:
    spec = spec_of(channel)
    photo = bool(persona.photo_enabled) and spec.can_send(MessageKind.PHOTO)
    video = bool(persona.video_enabled) and spec.can_send(MessageKind.VIDEO)
    tts, image, clip = (agent_config(persona, key).model
                        for key in (AgentKey.TTS, AgentKey.IMAGE, AgentKey.VIDEO))
    voice = bool(persona.voice_enabled and persona.voice_id and tts)
    return Capabilities(
        voice=voice and spec.can_send(MessageKind.VOICE),
        photo=photo,
        photo_generate=photo and bool(image),
        video=video,
        video_generate=video and bool(clip and image),
        video_note=spec.can_send(MessageKind.VIDEO_NOTE),
    )


def with_camera(caps: Capabilities, scene: SceneCard | None) -> Capabilities:
    """Add «she can not film a video note now» (driving, running, asleep…) from her scene."""
    camera = camera_for(scene)
    if camera.hold == CameraHold.IMPOSSIBLE:
        return replace(caps, camera_block=camera.reason)
    return caps
