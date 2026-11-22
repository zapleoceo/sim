"""Her constant details and her one apartment (docs/persona-life.md, «Облик и дом»): the owner saw
a different manicure and earrings on each photo and a new flat every time."""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import Chat, Generation, Persona, PersonaDay
from sim.domain.enums import DaySource, GenerationKind, GenerationStatus, MediaKind, MediaSource
from sim.domain.life import Ambience, BusyLevel, DayEvent, Home, Look, PersonaLife, Room
from sim.domain.plan import GenerateSpec
from sim.domain.scene import Light, SceneCard, ShotType
from sim.services.deps import Deps
from sim.services.generation import (
    GenerationService,
    reference_ids,
    room_ids,
    start_generation,
)
from sim.services.life.home import at_home, find_room, home_room
from sim.services.life.render import home_lines
from sim.services.media_library import NotAReference, update_media
from sim.services.visual import media_prompt
from tests.services.conftest import Fakes
from tests.services.factories import fresh, make_chat, make_media, make_persona
from tests.services.life_seed import SATURDAY, SEED, TZ, day_plan, utc

HOME = Home(details="old building in Dresden, 4th floor, high ceilings, white walls, oak floor",
            rooms=[Room(name="гостиная", details="grey sofa, two armchairs, a bookshelf"),
                   Room(name="спальня", details="white bed linen, sage-green wall"),
                   Room(name="кухня", details="white cabinets, small round table"),
                   Room(name="балкон", details="geraniums, two chairs, view of the Elbe")])
LOOK = Look(nails="short almond nails, nude-pink polish", earrings="small gold hoops",
            jewelry="thin gold chain, slim gold watch on the left wrist")


def scene(place: str = "дома", activity: str = "ужин и сериал") -> SceneCard:
    return SceneCard(day=date(2026, 11, 21), event_id="e", place=place, place_details="",
                     activity=activity, with_whom=(), outfit="серый свитер",
                     light=Light.EVENING_LAMP, ambience=Ambience.QUIET_HOME,
                     busy=BusyLevel.FREE, indoors=True)


def test_the_room_is_found_by_request_then_place_then_activity() -> None:
    assert home_room(HOME, scene(), "покажи свою спальню, сделай румтур").name == "спальня"
    assert home_room(HOME, scene(), "shows her bedroom").name == "спальня"
    assert home_room(HOME, scene("дома, балкон", "кофе"), "улыбается").name == "балкон"
    assert home_room(HOME, scene("дома", "готовит ужин"), "улыбается").name == "кухня"
    assert home_room(HOME, scene("дома, кухня"), "").name == "кухня"
    # Nothing points to a room: the first one.
    assert home_room(HOME, scene("дома", "читает"), "улыбается").name == "гостиная"


def test_other_places_are_not_her_apartment() -> None:
    for place in ("дома у Сони", "у мамы", "кофейня у дома", "по дороге домой",
                  "домик на даче", "садовый участок мамы", "рынок", ""):
        assert not at_home(HOME, place), place
        assert home_room(HOME, scene(place), "в спальне") is None
    assert at_home(HOME, "квартира") and at_home(HOME, "спальня")
    assert home_room(Home(), scene(), "спальня") is None           # no rooms described
    assert home_room(HOME, None, "спальня") is None
    assert find_room(HOME, "") is None


def test_the_photo_prompt_has_the_details_and_the_room() -> None:
    room = HOME.rooms[1]
    prompt = media_prompt(stage="image", action="показывает спальню", scene=scene(),
                          shot=ShotType.FRONT_SELFIE, identity_refs=4, continuity_refs=1,
                          look=LOOK, room=room, home=HOME, room_refs=2)
    assert ("do not change, add or remove them: nails (manicure): short almond nails, "
            "nude-pink polish; earrings: small gold hoops; jewellery and watch: thin gold "
            "chain") in prompt
    assert ("Place: her own apartment — спальня: white bed linen, sage-green wall. The "
            "apartment: old building in Dresden") in prompt
    assert "Reference image(s) 6–7: this same room of her apartment" in prompt
    video = media_prompt(stage="video", action="машет", scene=scene(),
                         shot=ShotType.FRONT_SELFIE, round_note=True, speech="Привет",
                         look=LOOK, room=room, home=HOME)
    assert "Place: her own apartment — спальня" in video
    assert "stay exactly as in the first frame (nails (manicure): short almond" in video
    # Nothing described: still «the same as in the references», never a new set.
    bare = media_prompt(stage="image", action="улыбается", scene=None,
                        shot=ShotType.FRONT_SELFIE)
    assert "exactly the same as in the reference images; do not invent new ones" in bare


def test_her_chat_context_knows_her_details_and_her_flat() -> None:
    lines = home_lines(PersonaLife(look=LOOK, home=HOME))
    assert lines[0].startswith("Твои постоянные детали (так на всех фото): маникюр — short")
    assert "серьги — small gold hoops" in lines[0]
    assert lines[1] == ("Твоя квартира (всегда одна и та же): old building in Dresden, 4th "
                        "floor, high ceilings, white walls, oak floor")
    assert lines[2:] == ["- гостиная: grey sofa, two armchairs, a bookshelf",
                         "- спальня: white bed linen, sage-green wall",
                         "- кухня: white cabinets, small round table",
                         "- балкон: geraniums, two chairs, view of the Elbe"]
    assert home_lines(PersonaLife()) == []


async def _persona(db: AsyncSession) -> Persona:
    life: dict[str, Any] = {**SEED["life"], "look": LOOK.model_dump(),
                            "home": HOME.model_dump()}
    return await make_persona(db, timezone=TZ, life=life)


async def _day(db: AsyncSession, persona: Persona, events: list[DayEvent]) -> None:
    db.add(PersonaDay(persona_id=persona.id, day=SATURDAY, summary="",
                      events=[e.model_dump(mode="json") for e in events],
                      source=DaySource.MODEL, model="life-model"))
    await db.flush()


async def _photo(db: AsyncSession, deps: Deps, chat: Chat, persona: Persona,
                 prompt: str) -> Generation:
    generation = await start_generation(
        db, persona=persona, chat_id=chat.id, reply_run_id=None, kind=GenerationKind.PHOTO,
        spec=GenerateSpec(prompt=prompt, caption="фото дома", tags=[]))
    assert await GenerationService(deps).run(generation.id) == GenerationStatus.DONE
    return await fresh(db, generation)


async def test_the_bedroom_looks_the_same_on_another_day(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await _persona(db)
    chat = await make_chat(db, persona)
    face = await make_media(db, persona.id, is_reference=True, storage=fakes.storage,
                            data=b"FACE")
    await _day(db, persona, day_plan())

    clock.at = utc(SATURDAY, "21:40")                        # reading in the bedroom
    first = await _photo(db, deps, chat, persona, "читает в кровати")
    assert first.scene["room"] == "спальня"
    assert first.ref_media_ids == [face.id]                  # the first bedroom photo: no refs
    prompt = fakes.image_gen.calls[0]["prompt"]
    assert "Place: her own apartment — спальня" in prompt
    assert "nails (manicure): short almond nails" in prompt

    clock.at = utc(SATURDAY, "10:00")                        # cleaning: the living room
    living = await _photo(db, deps, chat, persona, "улыбается")
    assert living.scene["room"] == "гостиная"

    clock.at = utc(SATURDAY, "19:30")      # dinner in the living room, but he asked for a tour
    tour = await _photo(db, deps, chat, persona, "показывает свою спальню, румтур")
    assert tour.scene["room"] == "спальня"
    # The earlier bedroom photo is a room reference even though it is another event.
    bedroom_photo = first.result_media_id
    assert bedroom_photo in tour.ref_media_ids
    tour_prompt = fakes.image_gen.calls[2]["prompt"]
    assert "Reference image(s) 3–3: this same room of her apartment" in tour_prompt
    # The living-room photo of the same evening gives only the clothes.
    assert "she is somewhere else now" in tour_prompt

    clock.at = utc(SATURDAY, "12:00")                        # the market: not at home
    market = await _photo(db, deps, chat, persona, "в спальне")
    assert "room" not in market.scene


# --- what drifted on production (2026-11-22): the face, the desk, the lamp, the window ---------

ROOM = Room(name="кухня", details="white handleless cabinets, a round wooden table",
            layout="the round table with two chairs by the tall window on the left",
            lamps="a brass mushroom table lamp on the windowsill",
            window_view="bare trees and old houses across a quiet street")


def test_the_room_layout_lamps_and_window_view_are_fixed_in_the_prompt() -> None:
    prompt = media_prompt(stage="image", action="пьёт чай", scene=scene("дома, кухня"),
                          shot=ShotType.FRONT_SELFIE, identity_refs=2, look=LOOK, room=ROOM,
                          home=HOME, room_refs=3)
    assert ("Room layout, fixed — the furniture stands exactly so: the round table with two "
            "chairs by the tall window on the left.") in prompt
    assert ("Lamps in this room, exactly these and no other lamps: a brass mushroom table "
            "lamp on the windowsill.") in prompt
    assert ("Outside the window, always this same view: bare trees and old houses across a "
            "quiet street.") in prompt
    assert "Reference image(s) 3–5: this same room of her apartment" in prompt
    # Only her face is taken from the identity references, not their jewellery or friends.
    assert "not their jewellery, clothes or background" in prompt
    video = media_prompt(stage="video", action="машет", scene=scene("дома, кухня"),
                         shot=ShotType.FRONT_SELFIE, round_note=True, room=ROOM, home=HOME)
    assert "Lamps in this room, exactly these" in video
    # An undescribed part adds no line.
    bare = media_prompt(stage="image", action="улыбается", scene=scene(),
                        shot=ShotType.FRONT_SELFIE, room=HOME.rooms[0], home=HOME)
    assert "Room layout" not in bare and "Outside the window" not in bare


def test_her_chat_context_knows_the_view_from_her_window() -> None:
    lines = home_lines(PersonaLife(home=Home(rooms=[ROOM])))
    assert lines[1] == ("- кухня: white handleless cabinets, a round wooden table; "
                        "расстановка — the round table with two chairs by the tall window on "
                        "the left; светильники — a brass mushroom table lamp on the windowsill; "
                        "вид из окна — bare trees and old houses across a quiet street")


async def test_a_pinned_room_photo_replaces_the_first_one(
        db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock) -> None:
    persona = await _persona(db)
    chat = await make_chat(db, persona)
    face = await make_media(db, persona.id, is_reference=True, storage=fakes.storage,
                            data=b"FACE")
    await _day(db, persona, day_plan())
    clock.at = utc(SATURDAY, "21:40")                        # reading in the bedroom
    first = await _photo(db, deps, chat, persona, "читает в кровати")
    second = await _photo(db, deps, chat, persona, "читает в кровати")
    third = await _photo(db, deps, chat, persona, "читает в кровати")
    assert second.result_media_id is not None and third.result_media_id is not None
    # Without a pin: the first and the latest photo of the room.
    assert await room_ids(db, persona.id, "спальня", [face.id]) == [
        first.result_media_id, third.result_media_id]

    await update_media(db, second.result_media_id, is_room_ref=True)
    fourth = await _photo(db, deps, chat, persona, "читает в кровати")
    # The pinned photo leads the room references; the first photo is no longer sent.
    assert fourth.ref_media_ids == [face.id, second.result_media_id, third.result_media_id]
    assert first.result_media_id not in fourth.ref_media_ids
    prompt = fakes.image_gen.calls[3]["prompt"]
    assert "Reference image(s) 2–3: this same room of her apartment" in prompt


async def test_a_generated_photo_can_be_her_face_reference(db: AsyncSession) -> None:
    persona = await _persona(db)
    uploaded = await make_media(db, persona.id, is_reference=True)
    best = await make_media(db, persona.id, source=MediaSource.GENERATED, is_reference=True)
    later = await make_media(db, persona.id, is_reference=True)
    await make_media(db, persona.id, source=MediaSource.GENERATED)           # not picked
    # Uploaded references first, then the generated ones the owner picked.
    assert await reference_ids(db, persona.id) == [uploaded.id, later.id, best.id]
    video = await make_media(db, persona.id, kind=MediaKind.VIDEO)
    with pytest.raises(NotAReference):
        await update_media(db, video.id, is_reference=True)
    with pytest.raises(NotAReference):                       # not a photo of her room
        await update_media(db, uploaded.id, is_room_ref=True)
