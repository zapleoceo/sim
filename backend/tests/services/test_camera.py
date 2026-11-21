"""How a video note is filmed right now: in her hand, propped up while her hands are busy, or
not at all (driving, running, asleep). Pure: scene in, camera out."""
from __future__ import annotations

import pytest

from sim.domain.life import BusyLevel
from sim.domain.plan import TextAction, VideoNoteAction
from sim.domain.scene import CameraHold, ShotType
from sim.services.capabilities import Capabilities, with_camera
from sim.services.plan_check import PlanError, validated_plan
from sim.services.visual import PROPPED_FRAMING, SELFIE_FRAMING, camera_for, media_prompt
from tests.services.factories import plan_json
from tests.services.test_scene import scene

ALL = Capabilities(voice=True, photo=True, photo_generate=True, video=True, video_generate=True)
NOTE = {"type": "video_note", "generate": {"prompt": "машу рукой", "caption": "привет",
                                           "speech": "Дима, привет!"}}


@pytest.mark.parametrize(("activity", "busy", "hold"), [
    ("читает Канемана", BusyLevel.FREE, CameraHold.HANDHELD),
    ("гуляет вдоль Эльбы", BusyLevel.FREE, CameraHold.HANDHELD),
    ("созвон с Соней", BusyLevel.PARTIAL, CameraHold.HANDHELD),
    ("готовит драники и ужинает", BusyLevel.PARTIAL, CameraHold.PROPPED),
    ("онлайн-консультации с клиентами", BusyLevel.PARTIAL, CameraHold.PROPPED),
    ("красит ногти", BusyLevel.FREE, CameraHold.PROPPED),
    ("едет за рулём к маме", BusyLevel.PARTIAL, CameraHold.IMPOSSIBLE),
    ("пробежка 5 км", BusyLevel.AWAY, CameraHold.IMPOSSIBLE),
    ("принимает душ", BusyLevel.AWAY, CameraHold.IMPOSSIBLE),
    ("сон", BusyLevel.AWAY, CameraHold.IMPOSSIBLE),
    ("помогает маме на участке", BusyLevel.AWAY, CameraHold.IMPOSSIBLE),
])
def test_camera_follows_what_she_is_doing(activity: str, busy: BusyLevel,
                                          hold: CameraHold) -> None:
    camera = camera_for(scene(activity=activity, busy=busy))
    assert camera.hold == hold
    assert activity in camera.reason


def test_without_a_scene_she_films_in_her_hand() -> None:
    assert camera_for(None).hold == CameraHold.HANDHELD


def test_a_propped_phone_is_a_static_frame_in_the_prompt() -> None:
    cooking = scene(activity="готовит драники")
    note = media_prompt(stage="video", action="улыбается", scene=cooking,
                        shot=ShotType.FRONT_SELFIE, speech="Привет!", language="ru",
                        round_note=True)
    assert note.startswith(PROPPED_FRAMING) and "static" in note
    reading = media_prompt(stage="video", action="улыбается", scene=scene(activity="читает"),
                           shot=ShotType.FRONT_SELFIE, speech="Привет!", language="ru",
                           round_note=True)
    assert reading.startswith(SELFIE_FRAMING)


def test_when_she_can_not_film_the_plan_must_answer_in_text() -> None:
    driving = with_camera(ALL, scene(activity="едет за рулём", busy=BusyLevel.PARTIAL))
    assert "за рулём" in driving.camera_block
    with pytest.raises(PlanError, match="ответь текстом"):
        validated_plan(plan_json(NOTE), catalog={}, caps=driving)
    plan = validated_plan(plan_json({"type": "text", "text": "Я за рулём, потом запишу)"}),
                          catalog={}, caps=driving)
    assert isinstance(plan.messages[0], TextAction)


def test_she_films_when_she_can() -> None:
    free = with_camera(ALL, scene(activity="читает Канемана"))
    assert free.camera_block == ""
    plan = validated_plan(plan_json(NOTE), catalog={}, caps=free)
    assert isinstance(plan.messages[0], VideoNoteAction)
