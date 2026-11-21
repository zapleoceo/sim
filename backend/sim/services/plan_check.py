"""Parse and validate the reply model's JSON plan (docs/reply.md). Pydantic checks the shape
(sim/domain/plan.py); this module adds what only the chat knows: media must come from this
chat's catalog, nothing may be sent twice, and only what the persona is allowed to do."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

from pydantic import ValidationError

from sim.domain.enums import MediaKind
from sim.domain.plan import (
    PhotoAction,
    ReplyPlan,
    VideoAction,
    VoiceAction,
    media_choice_errors,
)
from sim.services.capabilities import Capabilities

MAX_GENERATIONS_PER_TURN = 1
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class PlanError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def extract_json(text: str) -> str:
    """The JSON object inside the model's text (tolerates code fences and stray words)."""
    clean = _FENCE.sub("", text.strip())
    start, end = clean.find("{"), clean.rfind("}")
    if start == -1 or end < start:
        return clean
    return clean[start:end + 1]


def parse_plan(text: str) -> ReplyPlan:
    try:
        data = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        raise PlanError([f"это не JSON: {exc.msg}"]) from exc
    try:
        return ReplyPlan.model_validate(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in err['loc']) or 'plan'}: {err['msg']}"
                  for err in exc.errors()]
        raise PlanError(errors) from exc


def plan_errors(plan: ReplyPlan, *, catalog: Mapping[int, MediaKind],
                caps: Capabilities) -> list[str]:
    errors = media_choice_errors(plan)
    seen: set[int] = set()
    generations = 0
    for i, action in enumerate(plan.messages):
        where = f"messages[{i}]"
        if isinstance(action, VoiceAction) and not caps.voice:
            errors.append(f"{where}: голосовые сейчас нельзя, напиши текстом")
        if not isinstance(action, PhotoAction | VideoAction):
            continue
        is_photo = isinstance(action, PhotoAction)
        word = "фото" if is_photo else "видео"
        if not (caps.photo if is_photo else caps.video):
            errors.append(f"{where}: {word} сейчас отправлять нельзя")
            continue
        if action.generate is not None:
            generations += 1
            if not (caps.photo_generate if is_photo else caps.video_generate):
                errors.append(f"{where}: новые {word} сейчас делать нельзя, выбери из каталога")
        if action.media_id is not None:
            expected = MediaKind.PHOTO if is_photo else MediaKind.VIDEO
            kind = catalog.get(action.media_id)
            if kind is None:
                errors.append(f"{where}: media_id {action.media_id} нет в списке «Можно "
                              "отправить» (или уже отправлено в этот чат)")
            elif kind != expected:
                errors.append(f"{where}: media_id {action.media_id} — это не {word}")
            if action.media_id in seen:
                errors.append(f"{where}: media_id {action.media_id} уже есть в этом ответе")
            seen.add(action.media_id)
    if generations > MAX_GENERATIONS_PER_TURN:
        errors.append(f"не больше {MAX_GENERATIONS_PER_TURN} генерации за ход")
    return errors


def validated_plan(text: str, *, catalog: Mapping[int, MediaKind],
                   caps: Capabilities) -> ReplyPlan:
    """Parse + check; raises PlanError with every problem found."""
    plan = parse_plan(text)
    errors = plan_errors(plan, catalog=catalog, caps=caps)
    if errors:
        raise PlanError(errors)
    return plan
