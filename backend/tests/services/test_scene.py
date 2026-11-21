"""Visual and sound continuity (docs/persona-life.md, «Сцена и непрерывность»): the scene card,
the shot type, the one prompt builder and the voice-note background (pure)."""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from sim.adapters.ambience import CHATTER, PHONE_MIC, resolve, voice_note_args
from sim.domain.life import Ambience, BusyLevel
from sim.domain.scene import Light, SceneCard, ShotType
from sim.services.life.scene import (
    HOME_OUTFIT,
    SLEEP_OUTFIT,
    guess_ambience,
    light_at,
    voice_ambience,
)
from sim.services.visual import (
    NEGATIVE,
    SELFIE_FRAMING,
    choose_shot,
    media_prompt,
)
from tests.services.life_seed import SATURDAY, TZ, day_plan, life_state, utc

SUNDAY = date(2026, 11, 22)


def test_scene_follows_the_planned_event() -> None:
    scene = life_state("19:30", plans={SATURDAY: day_plan()}).scene()
    assert scene.event_id == "2026-11-21T19:00"
    assert (scene.place, scene.activity, scene.with_whom) == ("дома", "ужин и сериал",
                                                               ("Андрей",))
    assert scene.outfit == "серый домашний свитер и тёмные леггинсы"
    assert scene.ambience == Ambience.QUIET_HOME and scene.indoors
    assert scene.place_details.startswith("Дрезден, Йоханштадт")
    assert scene.light == Light.EVENING_LAMP
    # Two minutes later: the same event, the same card.
    assert life_state("19:32", plans={SATURDAY: day_plan()}).scene() == scene
    # The next event is a new scene.
    later = life_state("21:40", plans={SATURDAY: day_plan()}).scene()
    assert later.event_id == "2026-11-21T21:30" and later.outfit == scene.outfit


def test_scene_from_the_routine_with_a_neutral_outfit() -> None:
    call = life_state("20:10").scene()
    assert call.event_id == "routine:2026-11-21T20:00"
    assert call.activity == "созвон с Соней" and call.outfit == HOME_OUTFIT
    garden = life_state("15:00").scene()
    assert garden.ambience == Ambience.PARK and not garden.indoors
    assert garden.outfit == "a dark wool coat, a scarf, jeans and ankle boots"   # November
    night = life_state("02:00", day=SUNDAY).scene()
    assert night.busy == BusyLevel.AWAY and night.outfit == SLEEP_OUTFIT
    assert night.event_id == "routine:2026-11-21T23:00" and night.light == Light.NIGHT


def test_free_time_is_one_scene_at_home() -> None:
    first, second = life_state("19:00").scene(), life_state("19:20").scene()
    assert first.event_id == second.event_id == "2026-11-21:free"
    assert first.place == "дома" and first.ambience == Ambience.QUIET_HOME


@pytest.mark.parametrize(("place", "ambience"), [
    ("кофейня рядом с домом", Ambience.CAFE), ("дома, кухня", Ambience.KITCHEN),
    ("вдоль Эльбы или в Большом саду", Ambience.PARK), ("рынок, соседний подъезд",
                                                        Ambience.STREET),
    ("в трамвае", Ambience.TRANSPORT), ("дома, балкон", Ambience.QUIET_HOME),
    ("у Инги", Ambience.QUIET_HOME),
])
def test_ambience_from_the_place(place: str, ambience: Ambience) -> None:
    assert guess_ambience(place) == ambience


@pytest.mark.parametrize(("hhmm", "light"), [
    ("05:00", Light.NIGHT), ("08:00", Light.MORNING_DAYLIGHT), ("12:00", Light.DAYLIGHT),
    ("16:10", Light.GOLDEN_HOUR), ("19:30", Light.EVENING_LAMP), ("23:30", Light.NIGHT),
])
def test_light_by_local_time_and_season(hhmm: str, light: Light) -> None:
    assert light_at(utc(SATURDAY, hhmm).astimezone(ZoneInfo(TZ))) == light


# --- voice-note background ---------------------------------------------------------------------


def test_voice_background_comes_from_the_scene_tag() -> None:
    market = life_state("12:00", plans={SATURDAY: day_plan()})
    assert voice_ambience(market.scene(), market.local, enabled=True) == Ambience.STREET
    home_evening = life_state("19:30", plans={SATURDAY: day_plan()})
    assert voice_ambience(home_evening.scene(), home_evening.local,
                          enabled=True) == Ambience.QUIET_HOME
    assert voice_ambience(home_evening.scene(), home_evening.local, enabled=False) is None
    late = life_state("22:30", plans={SATURDAY: day_plan()})
    assert voice_ambience(late.scene(), late.local, enabled=True) == Ambience.NONE   # night


def test_voice_note_ffmpeg_args() -> None:
    clean = voice_note_args("none", 7)
    assert PHONE_MIC in clean[clean.index("-filter_complex") + 1] and "-i" not in clean
    assert clean[-1] == "ogg" and "libopus" in clean
    cafe = voice_note_args("cafe", 123)
    assert cafe[cafe.index("-i") + 1] == str(CHATTER) and CHATTER.is_file()
    assert voice_note_args("cafe", 123) == cafe                 # same seed, same bed
    assert voice_note_args("park", 5) != voice_note_args("park", 6)
    for value in Ambience:
        graph = voice_note_args(value.value, 9)
        assert "seed=" in graph[graph.index("-filter_complex") + 1] or value == Ambience.NONE
    assert resolve("mars") == Ambience.NONE


# --- shot type and the prompt builder ----------------------------------------------------------


def scene(**overrides: object) -> SceneCard:
    fields: dict[str, object] = {
        "day": SATURDAY, "event_id": "2026-11-21T19:00", "place": "дома",
        "place_details": "Йоханштадт, 4 этаж", "activity": "ужин и сериал",
        "with_whom": ("Андрей",), "outfit": "серый свитер", "light": Light.EVENING_LAMP,
        "ambience": Ambience.QUIET_HOME, "busy": BusyLevel.FREE, "indoors": True}
    fields.update(overrides)
    return SceneCard(**fields)  # type: ignore[arg-type]


def test_shot_type() -> None:
    assert choose_shot(scene(), "", spoken=False) == ShotType.FRONT_SELFIE
    assert choose_shot(scene(), "mirror", spoken=False) == ShotType.MIRROR_SELFIE
    assert choose_shot(scene(), "by_other", spoken=False) == ShotType.TAKEN_BY_OTHER
    assert choose_shot(scene(with_whom=()), "by_other", spoken=False) == ShotType.FRONT_SELFIE
    assert choose_shot(None, "by_other", spoken=False) == ShotType.FRONT_SELFIE
    assert choose_shot(scene(), "mirror", spoken=True) == ShotType.FRONT_SELFIE


def test_photo_prompt_has_appearance_scene_and_negatives() -> None:
    prompt = media_prompt(stage="image", action="улыбаюсь, держу чашку", scene=scene(),
                          shot=ShotType.FRONT_SELFIE, appearance="green eyes, long auburn hair",
                          identity_refs=4, continuity_refs=2)
    assert "first 4 reference image(s)" in prompt
    assert "Her appearance (keep exactly): green eyes, long auburn hair" in prompt
    assert "phone itself is NOT visible" in prompt
    assert "Place: дома — Йоханштадт, 4 этаж." in prompt
    assert "She wears: серый свитер — exactly this outfit." in prompt
    assert "Light: warm lamp light" in prompt and "With her now: Андрей" in prompt
    assert "Reference image(s) 5–6: the same room and the same clothes" in prompt
    assert "Pose, action and expression: улыбаюсь, держу чашку" in prompt
    assert prompt.endswith(NEGATIVE) and "a second phone" in NEGATIVE


def test_photo_by_someone_and_without_a_scene() -> None:
    by_other = media_prompt(stage="image", action="смеюсь", scene=scene(),
                            shot=ShotType.TAKEN_BY_OTHER)
    assert "taken by Андрей" in by_other and "With her now" not in by_other
    bare = media_prompt(stage="image", action="селфи у окна", scene=None,
                        shot=ShotType.FRONT_SELFIE)
    assert "Place:" not in bare and "Reference image(s)" not in bare
    assert "Pose, action and expression: селфи у окна" in bare


def test_video_prompt_speech_and_sound() -> None:
    note = media_prompt(stage="video", action="машу рукой", scene=scene(),
                        shot=ShotType.FRONT_SELFIE, speech="Дима,  привет!", language="ru",
                        round_note=True)
    assert note.startswith(SELFIE_FRAMING)
    assert "Scene: машу рукой" in note and "Sound: quiet room tone." in note
    assert "She says in Russian, exactly: «Дима, привет!»" in note
    assert "No subtitles, no music, no text on screen." in note
    assert "No second phone" in note
    silent = media_prompt(stage="video", action="танцую", scene=None,
                          shot=ShotType.FRONT_SELFIE, language="xx")
    assert silent.startswith("The video continues this photo") and "Sound:" not in silent
    spoken = media_prompt(stage="video", action="в парке", scene=None,
                          shot=ShotType.FRONT_SELFIE, speech="Смотри", language="xx")
    assert "in the chat language" in spoken
