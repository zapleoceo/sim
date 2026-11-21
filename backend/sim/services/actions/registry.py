"""Which handler executes which plan action. Add a handler here to support a new action."""
from __future__ import annotations

from pydantic import BaseModel

from sim.services.actions.base import ActionHandler
from sim.services.actions.media import PhotoHandler, VideoHandler
from sim.services.actions.text import TextHandler
from sim.services.actions.voice import VoiceHandler

HANDLERS: tuple[ActionHandler, ...] = (TextHandler(), VoiceHandler(), PhotoHandler(),
                                       VideoHandler())
_BY_TYPE: dict[type[BaseModel], ActionHandler] = {h.action_type: h for h in HANDLERS}


def handler_for(action: BaseModel) -> ActionHandler:
    try:
        return _BY_TYPE[type(action)]
    except KeyError:
        raise LookupError(f"no handler for action {type(action).__name__}") from None
