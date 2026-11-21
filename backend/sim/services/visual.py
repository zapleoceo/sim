"""The prompt of every generated photo, video and video note — ONE builder (docs/persona-life.md,
«Сцена и непрерывность»).

What the picture shows comes from code, not from the reply model:
- who: her appearance text (`persona.appearance`) and the identity references;
- where, in what clothes, in what light, who is around: the scene card of this moment;
- how it is taken: the shot type chosen by code (`choose_shot`);
- continuity: the latest photos of the same event are extra references, «same room, same
  clothes as this photo».
The reply model's own `prompt` is used only for her pose, action, expression and mood; it can
not move her to another place or change her clothes. Explicit negatives forbid a second phone
and extra hands (seen in production)."""
from __future__ import annotations

import re
from typing import Literal

from sim.domain.life import Ambience, BusyLevel
from sim.domain.scene import Camera, CameraHold, Light, SceneCard, ShotType

APPEARANCE_REFS = 4
CONTINUITY_REFS = 2
LANGUAGE_NAMES = {"ru": "Russian", "uk": "Ukrainian", "en": "English", "de": "German",
                  "es": "Spanish", "fr": "French", "it": "Italian", "pl": "Polish"}

SHOT_LINES = {
    ShotType.FRONT_SELFIE: (
        "Shot: a selfie taken with her phone's front camera held at arm's length. The phone "
        "itself is NOT visible in the picture; one of her hands may be out of frame."),
    ShotType.MIRROR_SELFIE: (
        "Shot: a mirror selfie. Her one phone is visible only in the mirror reflection, held in "
        "one hand."),
    ShotType.TAKEN_BY_OTHER: (
        "Shot: a casual phone photo of her taken by {who}, who is not in the picture. She is "
        "not holding a phone."),
}
SELFIE_FRAMING = (
    "Vertical selfie video filmed on her phone's front camera, held at arm's length; the phone "
    "itself is not visible. She looks straight into the lens. Natural light of the place she is "
    "in, casual and warm, slight handheld movement.")
PROPPED_FRAMING = (
    "Vertical video filmed on her phone's front camera propped up on a nearby surface at chest "
    "height (leaning against the wall, a cup or a stand); the frame is static, not handheld; the "
    "phone itself is not visible. Her hands are busy with what she is doing; she glances and "
    "leans toward the lens while talking. Natural light of the place, casual and warm.")
PHOTO_CONTINUES = ("The video continues this photo: the same woman, the same place, the same "
                   "clothes and light.")
LIGHT_WORDS = {
    Light.MORNING_DAYLIGHT: ("soft morning daylight", "soft morning daylight"),
    Light.DAYLIGHT: ("natural daylight from the window", "natural daylight"),
    Light.GOLDEN_HOUR: ("warm low evening sun through the window", "warm low evening sun"),
    Light.EVENING_LAMP: ("warm lamp light, dark outside the window", "dusk and street lights"),
    Light.NIGHT: ("dim warm night light, a single lamp", "night, street lights"),
}
AMBIENCE_SOUNDS = {
    Ambience.NONE: "quiet, only her voice",
    Ambience.QUIET_HOME: "quiet room tone",
    Ambience.KITCHEN: "soft kitchen sounds, a fridge hum",
    Ambience.STREET: "distant street traffic",
    Ambience.PARK: "light wind and birds",
    Ambience.CAFE: "soft cafe chatter in the background",
    Ambience.CAR: "low car engine hum",
    Ambience.TRANSPORT: "tram rumble and quiet passengers",
    Ambience.OFFICE: "quiet office hum",
}
# Ordinary phone quality instead of a studio look (owner's request): generated media must look
# like what a real person sends, not like an advert.
PHONE_PHOTO = (
    "Quality: an ordinary amateur smartphone photo, not a professional shoot — no studio "
    "lighting, no retouching, natural skin texture with pores and small imperfections, "
    "slightly imperfect casual framing (a little tilt or off-centre), mild front-camera "
    "wide-angle distortion, light JPEG compression.")
PHONE_VIDEO = (
    "Quality: an ordinary smartphone clip, not a cinematic shot — no stabilizer, no studio "
    "light, autofocus and auto-exposure adjusting a little as she moves.")
LIGHT_QUALITY = {
    Light.EVENING_LAMP: "low indoor light: visible noise and grain, slightly soft focus, "
                        "warm uneven lamp light",
    Light.NIGHT: "very low light: strong noise and grain, soft focus, a single dim lamp",
    Light.DAYLIGHT: "window light: a slightly overexposed window, uneven shadows",
    Light.MORNING_DAYLIGHT: "soft morning light, slightly flat colours",
    Light.GOLDEN_HOUR: "low sun: lens flare, one side of the face in shadow",
}
HANDHELD_MOTION = "Natural hand shake and small framing jumps of a phone held in one hand."
PROPPED_MOTION = ("The phone stands still but the frame is slightly crooked; exposure shifts "
                  "when she leans in or moves away.")
_ON_THE_MOVE = re.compile(r"\b(ид[её]т|идёт|гуля|едет|шага|по дороге|на ходу|возвраща)",
                          re.IGNORECASE)
_AFTER_SPORT = re.compile(r"\b(после пробежк|после трениров|пробежк|бег)", re.IGNORECASE)


def realism_lines(scene: SceneCard | None, *, video: bool,
                  camera: Camera | None = None) -> list[str]:
    """How ordinary the picture looks, from the scene: low light gives grain, walking gives a
    bounce or a little motion blur, right after a run she is flushed and out of breath."""
    lines = [PHONE_VIDEO if video else PHONE_PHOTO]
    if scene is None:
        return lines
    lines.append(f"Light quality: {LIGHT_QUALITY[scene.light]}.")
    activity = scene.activity.lower()
    moving = bool(_ON_THE_MOVE.search(activity))
    if _AFTER_SPORT.search(activity):
        lines.append("She has just finished running: flushed cheeks, messy hair, a little "
                     "sweat" + (", out of breath, speaking with short pauses." if video
                                else "."))
    if video:
        hold = camera.hold if camera is not None else None
        if hold == CameraHold.PROPPED:
            lines.append(PROPPED_MOTION)
        else:
            lines.append(HANDHELD_MOTION + (" The frame bounces with her steps." if moving
                                            else ""))
    elif moving:
        lines.append("A little motion blur: she is on the move.")
    return lines


IDENTITY = ("The same woman as in the first {n} reference image(s): the same face, eye colour, "
            "hair and build.")
CONTINUITY = ("Reference image(s) {first}–{last}: the same room and the same clothes as this "
              "photo — keep the place and the outfit exactly as there.")
CONTINUITY_CLOTHES = ("Reference image(s) {first}–{last}: the same clothes as in this photo (she is "
                      "somewhere else now) — keep the outfit exactly as there.")
FIXED_SCENE = ("The place, the clothes and the light above are fixed; ignore any other place "
               "or clothes mentioned in the pose line.")
NEGATIVE = ("Avoid: a second phone, a phone in the other hand, extra hands or fingers, a "
            "different person from the references, text and watermarks.")
VIDEO_NEGATIVE = "No second phone and no extra hands in the frame."
SPEECH_LINE = ("She says in {language}, exactly: «{speech}». Her lips are in sync with the "
               "words.")
NO_OVERLAYS = "No subtitles, no music, no text on screen."

Stage = Literal["image", "video"]


# Word starts in her current activity that decide how a video note can be filmed.
_CANNOT_FILM = re.compile(
    r"\b(за рул|вед[уёе]т машин|веду машин|бе[гж]и|бегает|бежит|(?<!после )пробежк|"
    r"(?<!после )тренировк|плава|душ\b|"
    r"ванн|сон\b|сплю|спит|спать)", re.IGNORECASE)
_HANDS_BUSY = re.compile(
    r"\b(готов|жар|варит|печ[её]т|печь|режет|мо[ею]т посуд|ужина|обеда|завтрака|работа|"
    r"консульт|ноутбук|красит|макияж|вяж|шь[её]т|глад|убира|грядк|сажа|полива)", re.IGNORECASE)


def camera_for(scene: SceneCard | None) -> Camera:
    """How she can film a video note right now, with the reason (docs/persona-life.md). Without
    a scene: handheld, the usual way."""
    if scene is None:
        return Camera(CameraHold.HANDHELD, "обычный кружок с руки")
    activity = scene.activity.lower()
    if _CANNOT_FILM.search(activity) or scene.busy == BusyLevel.AWAY:
        return Camera(CameraHold.IMPOSSIBLE,
                      f"сейчас она {scene.activity} — снять кружок не может")
    if _HANDS_BUSY.search(activity):
        return Camera(CameraHold.PROPPED,
                      f"телефон прислонён, руки заняты: {scene.activity}")
    return Camera(CameraHold.HANDHELD, f"кружок с руки: {scene.activity}")


def choose_shot(scene: SceneCard | None, hint: str, *, spoken: bool) -> ShotType:
    """A spoken video (video note) is always a front selfie; a photo «by someone» needs someone
    from the scene; a mirror selfie when asked; otherwise a front selfie."""
    if spoken:
        return ShotType.FRONT_SELFIE
    if hint == "by_other" and scene is not None and scene.with_whom:
        return ShotType.TAKEN_BY_OTHER
    if hint == "mirror":
        return ShotType.MIRROR_SELFIE
    return ShotType.FRONT_SELFIE


def _scene_lines(scene: SceneCard, shot: ShotType, *, video: bool) -> list[str]:
    where = scene.place + (f" — {scene.place_details}" if scene.place_details else "")
    light = LIGHT_WORDS[scene.light][0 if scene.indoors else 1]
    lines = [f"Place: {where}.", f"She is: {scene.activity}.",
             f"She wears: {scene.outfit} — exactly this outfit.", f"Light: {light}."]
    if scene.with_whom and shot != ShotType.TAKEN_BY_OTHER:
        lines.append(f"With her now: {', '.join(scene.with_whom)} (they may stay out of frame).")
    if video:
        lines.append(f"Sound: {AMBIENCE_SOUNDS[scene.ambience]}.")
    return lines


def media_prompt(*, stage: Stage, action: str, scene: SceneCard | None, shot: ShotType,
                 appearance: str = "", identity_refs: int = 0, continuity_refs: int = 0,
                 speech: str = "", language: str = "", round_note: bool = False,
                 camera: Camera | None = None, continuity_same_place: bool = True) -> str:
    """The Broker prompt: `image` = the photo (also the first frame of a video), `video` = the
    clip made from that photo."""
    action = " ".join(action.split())
    if stage == "video":
        return _video(action, scene, shot, speech=speech, language=language,
                      round_note=round_note, camera=camera or camera_for(scene))
    lines = [IDENTITY.format(n=identity_refs) if identity_refs else "A photo of a young woman."]
    if appearance.strip():
        lines.append(f"Her appearance (keep exactly): {' '.join(appearance.split())}")
    who = ", ".join(scene.with_whom) if scene is not None and scene.with_whom else "a friend"
    lines.append(SHOT_LINES[shot].format(who=who))
    if scene is not None:
        lines += _scene_lines(scene, shot, video=False)
    if continuity_refs:
        template = CONTINUITY if continuity_same_place else CONTINUITY_CLOTHES
        lines.append(template.format(first=identity_refs + 1,
                                     last=identity_refs + continuity_refs))
    lines.append(f"Pose, action and expression: {action}")
    lines += realism_lines(scene, video=False)
    if scene is not None:
        lines.append(FIXED_SCENE)
    lines.append(NEGATIVE)
    return "\n".join(lines)


def _video(action: str, scene: SceneCard | None, shot: ShotType, *, speech: str, language: str,
           round_note: bool, camera: Camera) -> str:
    words = " ".join(speech.split())
    # A video note or a video in which she speaks is filmed by her for this person: in her hand,
    # or propped up when her hands are busy (camera_for).
    if round_note or words:
        framing = PROPPED_FRAMING if camera.hold == CameraHold.PROPPED else SELFIE_FRAMING
    else:
        framing = PHOTO_CONTINUES
    lines = [framing, f"Scene: {action}"]
    if scene is not None:
        lines += _scene_lines(scene, shot, video=True)
    if words:
        lines.append(SPEECH_LINE.format(
            language=LANGUAGE_NAMES.get(language, "the chat language"), speech=words))
    lines += realism_lines(scene, video=True, camera=camera)
    lines += [NO_OVERLAYS, VIDEO_NEGATIVE]
    return "\n".join(lines)
