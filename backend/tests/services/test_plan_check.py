from __future__ import annotations

import pytest

from sim.domain.enums import MediaKind
from sim.domain.plan import PhotoAction, TextAction
from sim.services.capabilities import Capabilities
from sim.services.plan_check import PlanError, extract_json, parse_plan, validated_plan
from tests.services.factories import plan_json

ALL = Capabilities(voice=True, photo=True, photo_generate=True, video=True, video_generate=True)
NONE = Capabilities(voice=False, photo=False, photo_generate=False, video=False,
                    video_generate=False)
CATALOG = {14: MediaKind.PHOTO, 15: MediaKind.VIDEO}
GEN = {"prompt": "селфи у окна", "caption": "селфи у окна", "tags": ["селфи"]}


def errors_of(text: str, caps: Capabilities = ALL) -> list[str]:
    with pytest.raises(PlanError) as info:
        validated_plan(text, catalog=CATALOG, caps=caps)
    return info.value.errors


def test_extract_json_tolerates_fences_and_words() -> None:
    assert extract_json('```json\n{"messages": []}\n```') == '{"messages": []}'
    assert extract_json('вот план: {"messages": []} всё') == '{"messages": []}'


def test_valid_plan_passes() -> None:
    plan = validated_plan(
        plan_json({"type": "text", "text": "привет"},
                  {"type": "photo", "media_id": 14, "caption": "это я"},
                  {"type": "video", "generate": GEN},
                  {"type": "voice", "text": "приветик"}, remember=["Живёт в Киеве"]),
        catalog=CATALOG, caps=ALL)
    assert isinstance(plan.messages[0], TextAction)
    assert isinstance(plan.messages[1], PhotoAction)
    assert plan.remember[0].fact == "Живёт в Киеве"


def test_empty_plan_is_silence() -> None:
    assert parse_plan('{"messages": []}').messages == []


def test_not_json_and_wrong_shape() -> None:
    assert errors_of("привет!")[0].startswith("это не JSON")
    assert errors_of('{"messages": [{"type": "text", "text": "a", "extra": 1}]}')
    assert errors_of('{"messages": [{"type": "sticker"}]}')


def test_media_must_come_from_the_catalog() -> None:
    errors = errors_of(plan_json({"type": "photo", "media_id": 99}))
    assert "нет в списке" in errors[0]
    errors = errors_of(plan_json({"type": "photo", "media_id": 15}))
    assert "это не фото" in errors[0]
    errors = errors_of(plan_json({"type": "photo", "media_id": 14},
                                 {"type": "photo", "media_id": 14}))
    assert "уже есть в этом ответе" in errors[0]


def test_exactly_one_of_media_id_or_generate() -> None:
    assert errors_of(plan_json({"type": "photo"}))
    assert errors_of(plan_json({"type": "photo", "media_id": 14, "generate": GEN}))


def test_disabled_capabilities_are_rejected() -> None:
    caps = Capabilities(voice=False, photo=True, photo_generate=False, video=True,
                        video_generate=False)
    assert "голосовые" in errors_of(plan_json({"type": "voice", "text": "а"}), caps)[0]
    assert "новые фото" in errors_of(plan_json({"type": "photo", "generate": GEN}), caps)[0]
    assert "новые видео" in errors_of(plan_json({"type": "video", "generate": GEN}), caps)[0]
    assert "фото сейчас" in errors_of(plan_json({"type": "photo", "media_id": 14}), NONE)[0]


def test_one_generation_per_turn() -> None:
    errors = errors_of(plan_json({"type": "photo", "generate": GEN},
                                 {"type": "video", "generate": GEN}))
    assert any("генерации" in error for error in errors)
