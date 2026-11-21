"""`scene_at`: where she is, what she wears, the light and the sound around her at a moment
(docs/persona-life.md, «Сцена и непрерывность»). Every photo, video, video note and voice note
reads this one card, so two pictures of the same event never disagree. Pure functions."""
from __future__ import annotations

from datetime import datetime

from sim.db.models import Persona
from sim.domain.life import Ambience, BusyLevel, PersonaLife
from sim.domain.scene import Light, SceneCard
from sim.services.context.facts import to_local
from sim.services.life.agenda import Agenda, Slot
from sim.services.life.world import life_of

HOME_PLACE = "дома"
# Approximate sunrise / sunset (local clock, daylight saving included) at ~51°N per month.
SUN_HOURS: tuple[tuple[float, float], ...] = (
    (8.2, 16.4), (7.5, 17.4), (6.6, 18.3), (6.6, 20.1), (5.5, 20.9), (5.0, 21.5),
    (5.3, 21.3), (6.0, 20.5), (6.8, 19.4), (7.6, 18.3), (7.3, 16.5), (8.0, 16.0),
)
NIGHT_FROM_HOUR = 23
QUIET_NIGHT = (22 * 60, 7 * 60)        # 22:00-07:00: a quiet home sounds like nothing
# Words of a place -> its sound, checked in this order (Russian place names).
AMBIENCE_WORDS: tuple[tuple[Ambience, tuple[str, ...]], ...] = (
    (Ambience.KITCHEN, ("кухн",)),
    (Ambience.CAFE, ("кафе", "кофейн", "ресторан", "пекарн", "бар")),
    (Ambience.CAR, ("машин", "авто")),
    (Ambience.TRANSPORT, ("трамва", "автобус", "поезд", "метро", "электричк")),
    (Ambience.PARK, ("парк", "сад", "эльб", "лес", "дач", "стадион", "участ")),
    (Ambience.OFFICE, ("офис", "работ", "коворк")),
    (Ambience.STREET, ("улиц", "рынок", "магазин", "город", "центр")),
    (Ambience.QUIET_HOME, ("дом", "квартир", "балкон", "спальн", "у мамы")),
)
INDOOR = frozenset({Ambience.NONE, Ambience.QUIET_HOME, Ambience.KITCHEN, Ambience.CAFE,
                    Ambience.OFFICE, Ambience.CAR, Ambience.TRANSPORT})
SLEEP_OUTFIT = "soft cotton pyjamas"
HOME_OUTFIT = "simple home clothes: a soft grey knit sweater and dark leggings"
# Outside, by month: cold (November-March), spring / autumn, summer.
OUT_OUTFITS = {
    "winter": "a dark wool coat, a scarf, jeans and ankle boots",
    "mid": "a light jacket over a sweater, jeans and sneakers",
    "summer": "a light summer dress and sandals",
}


def guess_ambience(place: str) -> Ambience:
    text = place.lower()
    for ambience, words in AMBIENCE_WORDS:
        if any(word in text for word in words):
            return ambience
    return Ambience.QUIET_HOME       # unknown places are taken as somebody's home


def light_at(local: datetime) -> Light:
    sunrise, sunset = SUN_HOURS[local.month - 1]
    hour = local.hour + local.minute / 60
    if hour >= NIGHT_FROM_HOUR or hour < max(sunrise - 1.5, 4.0):
        return Light.NIGHT
    if hour < sunrise + 3:
        return Light.MORNING_DAYLIGHT if hour >= sunrise else Light.EVENING_LAMP
    if hour < sunset - 1:
        return Light.DAYLIGHT
    if hour < sunset + 0.5:
        return Light.GOLDEN_HOUR
    return Light.EVENING_LAMP


def default_outfit(local: datetime, indoors: bool, busy: BusyLevel) -> str:
    if indoors and busy == BusyLevel.AWAY and light_at(local) == Light.NIGHT:
        return SLEEP_OUTFIT
    if indoors:
        return HOME_OUTFIT
    if local.month in (11, 12, 1, 2, 3):
        return OUT_OUTFITS["winter"]
    if local.month in (6, 7, 8):
        return OUT_OUTFITS["summer"]
    return OUT_OUTFITS["mid"]


def place_details(life: PersonaLife, place: str) -> str:
    """Details of the world's place whose name the slot's place mentions (first match)."""
    text = place.lower()
    for item in life.places:
        name = item.name.lower().split("(")[0].strip()
        if name and (name in text or text in name):
            return item.details
    return ""


def scene_at(now: datetime, day: Agenda, persona: Persona) -> SceneCard:
    """The scene at `now` from the agenda of her local day (plan, else routine). A gap in the
    day is time at home: one scene for every gap of the day."""
    local = to_local(now, persona.timezone)
    life = life_of(persona)
    slot = day.at(day.minute_of(local))
    if slot is None:
        slot = Slot(start=0, end=0, title="свободное время", place=HOME_PLACE,
                    ambience=Ambience.QUIET_HOME, key=f"{day.day.isoformat()}:free")
    ambience = slot.ambience or guess_ambience(slot.place)
    indoors = ambience in INDOOR
    return SceneCard(
        day=day.day, event_id=slot.key, place=slot.place or HOME_PLACE,
        place_details=place_details(life, slot.place or HOME_PLACE), activity=slot.title,
        with_whom=slot.with_whom,
        outfit=slot.outfit.strip() or default_outfit(local, indoors, slot.busy),
        light=light_at(local), ambience=ambience, busy=slot.busy, indoors=indoors,
    )


def voice_ambience(scene: SceneCard, local: datetime, *, enabled: bool) -> Ambience | None:
    """The background of a voice note: None = a clean recording (the owner turned it off);
    `none` = only the phone microphone. A quiet home at night is silent too."""
    if not enabled:
        return None
    minute = local.hour * 60 + local.minute
    night = minute >= QUIET_NIGHT[0] or minute < QUIET_NIGHT[1]
    if scene.ambience == Ambience.QUIET_HOME and night:
        return Ambience.NONE
    return scene.ambience
