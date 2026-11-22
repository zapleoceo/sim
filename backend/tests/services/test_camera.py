"""How a video note is filmed right now: in her hand, propped up while her hands are busy, or
not at all (driving, running, asleep). Pure: scene in, camera out."""
from __future__ import annotations

import pytest

from sim.domain.life import BusyLevel
from sim.domain.plan import TextAction, VideoNoteAction
from sim.domain.scene import Camera, CameraHold, Light, SceneCard, ShotType
from sim.services.capabilities import Capabilities, with_camera
from sim.services.plan_check import PlanError, validated_plan
from sim.services.visual import (
    HANDHELD_MOTION,
    PROPPED_FRAMING,
    SELFIE_FRAMING,
    SHOW_AROUND,
    camera_for,
    media_prompt,
)
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


# --- ordinary phone quality ------------------------------------------------------------------


def test_photos_look_like_ordinary_phone_photos() -> None:
    evening = media_prompt(stage="image", action="улыбается", scene=scene(),
                           shot=ShotType.FRONT_SELFIE)
    assert "amateur smartphone photo" in evening and "no retouching" in evening
    assert "noise and grain" in evening                      # the evening lamp light
    walking = media_prompt(stage="image", action="улыбается", shot=ShotType.FRONT_SELFIE,
                           scene=scene(activity="гуляет вдоль Эльбы", light=Light.DAYLIGHT,
                                       indoors=False))
    assert "motion blur" in walking and "overexposed window" in walking
    bare = media_prompt(stage="image", action="у окна", scene=None, shot=ShotType.FRONT_SELFIE)
    assert "amateur smartphone photo" in bare and "Light quality" not in bare


def test_video_shakes_in_her_hand_and_bounces_on_the_move() -> None:
    walking = media_prompt(stage="video", action="машет", shot=ShotType.FRONT_SELFIE,
                           scene=scene(activity="идёт по рынку"), speech="Привет!",
                           language="ru", round_note=True)
    assert HANDHELD_MOTION in walking and "bounces with her steps" in walking
    assert "Not smooth, not a gimbal" in walking
    cooking = media_prompt(stage="video", action="машет", shot=ShotType.FRONT_SELFIE,
                           scene=scene(activity="готовит драники"), speech="Привет!",
                           language="ru", round_note=True)
    assert "slightly crooked" in cooking and HANDHELD_MOTION not in cooking


def note_prompt(action: str, *, place: SceneCard | None = None,
                camera: Camera | None = None, round_note: bool = True,
                speech: str = "Привет!") -> str:
    return media_prompt(stage="video", action=action, scene=place, shot=ShotType.FRONT_SELFIE,
                        speech=speech, language="ru", round_note=round_note, camera=camera)


def test_a_handheld_note_shakes_even_without_a_scene() -> None:
    bare = note_prompt("машет")
    assert bare.startswith(SELFIE_FRAMING) and "not on a gimbal" in bare
    assert HANDHELD_MOTION in bare and "Light quality" not in bare
    propped = note_prompt("машет", camera=Camera(CameraHold.PROPPED, "красит ногти"))
    assert propped.startswith(PROPPED_FRAMING) and HANDHELD_MOTION not in propped


@pytest.mark.parametrize("action", ["показывает вид из окна", "показывает кухню",
                                    "shows the view from the balcony"])
def test_she_turns_the_phone_to_what_she_shows(action: str) -> None:
    assert SHOW_AROUND in note_prompt(action)
    assert SHOW_AROUND in note_prompt(action, place=scene(activity="читает"))
    # A video in which she speaks is filmed by her too.
    assert SHOW_AROUND in note_prompt(action, round_note=False)


def test_no_pan_without_show_words_or_on_a_propped_phone() -> None:
    assert SHOW_AROUND not in note_prompt("машет и улыбается")
    propped = Camera(CameraHold.PROPPED, "готовит драники")
    assert SHOW_AROUND not in note_prompt("показывает кухню", camera=propped)
    assert SHOW_AROUND not in note_prompt("показывает кухню",
                                          place=scene(activity="готовит драники"))
    # A silent clip that continues a photo is not filmed by her: no pan.
    assert SHOW_AROUND not in note_prompt("показывает кухню", round_note=False, speech="")


def test_right_after_a_run_she_is_out_of_breath() -> None:
    after = scene(activity="вернулась после пробежки, пьёт воду")
    clip = media_prompt(stage="video", action="машет", shot=ShotType.FRONT_SELFIE, scene=after,
                        speech="Привет!", language="ru", round_note=True)
    assert "out of breath" in clip and "flushed cheeks" in clip
