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

from typing import Literal

from sim.domain.life import Ambience
from sim.domain.scene import Light, SceneCard, ShotType

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
IDENTITY = ("The same woman as in the first {n} reference image(s): the same face, eye colour, "
            "hair and build.")
CONTINUITY = ("Reference image(s) {first}–{last}: the same room and the same clothes as this "
              "photo — keep the place and the outfit exactly as there.")
FIXED_SCENE = ("The place, the clothes and the light above are fixed; ignore any other place "
               "or clothes mentioned in the pose line.")
NEGATIVE = ("Avoid: a second phone, a phone in the other hand, extra hands or fingers, a "
            "different person from the references, text and watermarks.")
VIDEO_NEGATIVE = "No second phone and no extra hands in the frame."
SPEECH_LINE = ("She says in {language}, exactly: «{speech}». Her lips are in sync with the "
               "words.")
NO_OVERLAYS = "No subtitles, no music, no text on screen."

Stage = Literal["image", "video"]


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
                 speech: str = "", language: str = "", round_note: bool = False) -> str:
    """The Broker prompt: `image` = the photo (also the first frame of a video), `video` = the
    clip made from that photo."""
    action = " ".join(action.split())
    if stage == "video":
        return _video(action, scene, shot, speech=speech, language=language,
                      round_note=round_note)
    lines = [IDENTITY.format(n=identity_refs) if identity_refs else "A photo of a young woman."]
    if appearance.strip():
        lines.append(f"Her appearance (keep exactly): {' '.join(appearance.split())}")
    who = ", ".join(scene.with_whom) if scene is not None and scene.with_whom else "a friend"
    lines.append(SHOT_LINES[shot].format(who=who))
    if scene is not None:
        lines += _scene_lines(scene, shot, video=False)
    if continuity_refs:
        lines.append(CONTINUITY.format(first=identity_refs + 1,
                                       last=identity_refs + continuity_refs))
    lines.append(f"Pose, action and expression: {action}")
    if scene is not None:
        lines.append(FIXED_SCENE)
    lines.append(NEGATIVE)
    return "\n".join(lines)


def _video(action: str, scene: SceneCard | None, shot: ShotType, *, speech: str, language: str,
           round_note: bool) -> str:
    words = " ".join(speech.split())
    # A video note or a video in which she speaks is a selfie for this person.
    framing = SELFIE_FRAMING if round_note or words else PHOTO_CONTINUES
    lines = [framing, f"Scene: {action}"]
    if scene is not None:
        lines += _scene_lines(scene, shot, video=True)
    if words:
        lines.append(SPEECH_LINE.format(
            language=LANGUAGE_NAMES.get(language, "the chat language"), speech=words))
    lines += [NO_OVERLAYS, VIDEO_NEGATIVE]
    return "\n".join(lines)
