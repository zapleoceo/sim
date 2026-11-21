"""Which room of her own apartment a picture is taken in (docs/persona-life.md, «Облик и дом»).

Her home is ONE place (`persona.life.home`): every photo, video and video note taken there
shows one of its rooms with that room's description, and the earlier pictures of the same room
are references (sim/services/generation.room_ids). The room is found by words, in this order:
the request (the reply model's pose line: «покажи спальню»), the place of the scene, her
activity («готовит ужин» -> the kitchen); nothing found -> the first room. Pure functions."""
from __future__ import annotations

import re

from sim.domain.life import Ambience, Home, Room
from sim.domain.scene import SceneCard
from sim.services.life.scene import guess_ambience

# A room whose name holds the first word gets the other words as synonyms (Russian stems and
# English words: the pose line may be in either).
ROOM_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("спальн", ("спальн", "bedroom", "кроват", "постел", "пижам", "pyjama", "сплю", "проснул",
                "перед сном", "в кровати", "in bed")),
    ("кухн", ("кухн", "kitchen", "готовит", "готовл", "готовк", "cooking", "плит", "stove",
              "посуд")),
    ("ванн", ("ванн", "bathroom", "душ", "shower")),
    ("балкон", ("балкон", "balcony", "герань")),
    ("прихож", ("прихож", "коридор", "hallway")),
    ("гостин", ("гостин", "living room", "диван", "sofa", "couch", "телевиз")),
    ("кабинет", ("кабинет", "рабоч", "study", "desk", "ноутбук", "laptop")),
)
HOME_WORDS = ("дом", "квартир", "home")
HOME_SOUNDS = frozenset({Ambience.QUIET_HOME, Ambience.KITCHEN})
# «дома у Сони», «у мамы», «домик на даче», «по дороге домой» are not her apartment.
NOT_HER_HOME = re.compile(
    r"\bу\s+(?:[А-ЯЁ]|мам|пап|сестр|брат|подруг|друз|бабушк|дед|доч|сын|сосед|коллег)"
    r"|домик|дач|домой")


def _stem(word: str) -> str:
    """«спальня» -> «спаль», «кухня» -> «кухн»: matches other cases of the word."""
    return word[:max(4, len(word) - 2)]


def room_words(room: Room) -> tuple[str, ...]:
    name = room.name.lower()
    words = [_stem(word) for word in re.findall(r"\w+", name) if len(word) >= 4]
    for key, synonyms in ROOM_WORDS:
        if key in name:
            words += synonyms
    return tuple(dict.fromkeys(words))


def find_room(home: Home, text: str) -> Room | None:
    """The first room any of whose words appears in `text`."""
    lowered = " ".join(text.lower().split())
    if not lowered:
        return None
    for room in home.rooms:
        if any(word in lowered for word in room_words(room)):
            return room
    return None


def at_home(home: Home, place: str) -> bool:
    """True when the scene's place is her own apartment (or one of its rooms)."""
    text = place.lower()
    if not text or NOT_HER_HOME.search(text):
        return False
    if guess_ambience(text) not in HOME_SOUNDS:          # «кофейня у дома», «рынок»
        return False
    return any(word in text for word in HOME_WORDS) or find_room(home, text) is not None


def home_room(home: Home, scene: SceneCard | None, request: str) -> Room | None:
    """The room of her apartment this picture is taken in, or None when she is not at home
    (or no rooms are described)."""
    if scene is None or not home.rooms or not at_home(home, scene.place):
        return None
    for text in (request, scene.place, scene.activity):
        room = find_room(home, text)
        if room is not None:
            return room
    return home.rooms[0]
