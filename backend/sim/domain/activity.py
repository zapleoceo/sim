"""What her current activity says about her hands, her movement and her screen: word stems of the
Russian activity line of the scene («готовит ужин», «едет в трамвае», «работает за ноутбуком»).

One place for every consumer — how a video note is filmed and how real a photo looks
(sim/services/visual.py), how she types right now (sim/services/writing.py). Pure."""
from __future__ import annotations

import re

ON_THE_MOVE = re.compile(r"\b(ид[её]т|идёт|гуля|едет|шага|по дороге|на ходу|возвраща)",
                         re.IGNORECASE)
IN_TRANSIT = re.compile(r"\b(трамва|автобус|метро|электричк|поезд|такси|лифт|в машине|"
                        r"в очереди|на остановк)", re.IGNORECASE)
AFTER_SPORT = re.compile(r"\b(после пробежк|после трениров|пробежк|бег)", re.IGNORECASE)
CANNOT_FILM = re.compile(
    r"\b(за рул|вед[уёе]т машин|веду машин|бе[гж]и|бегает|бежит|(?<!после )пробежк|"
    r"(?<!после )тренировк|плава|душ\b|"
    r"ванн|сон\b|сплю|спит|спать)", re.IGNORECASE)
HANDS_BUSY = re.compile(
    r"\b(готов|жар|варит|печ[её]т|печь|режет|мо[ею]т посуд|ужина|обеда|завтрака|работа|"
    r"консульт|ноутбук|красит|макияж|вяж|шь[её]т|глад|убира|грядк|сажа|полива)", re.IGNORECASE)
AT_LAPTOP = re.compile(r"\b(ноутбук|компьютер|за столом с|работа|консульт|почт|отч[её]т|"
                       r"таблиц|созвон по работе|zoom|зум)", re.IGNORECASE)
ON_A_CALL = re.compile(r"\b(созвон|звонит|разговаривает по телефону|по телефону|на связи с)",
                       re.IGNORECASE)


def on_the_move(activity: str) -> bool:
    return bool(ON_THE_MOVE.search(activity))


def in_transit(activity: str, place: str = "") -> bool:
    return bool(IN_TRANSIT.search(activity) or IN_TRANSIT.search(place))


def after_sport(activity: str) -> bool:
    return bool(AFTER_SPORT.search(activity))


def cannot_film(activity: str) -> bool:
    return bool(CANNOT_FILM.search(activity))


def hands_busy(activity: str) -> bool:
    return bool(HANDS_BUSY.search(activity))


def at_laptop(activity: str) -> bool:
    return bool(AT_LAPTOP.search(activity))


def on_a_call(activity: str) -> bool:
    return bool(ON_A_CALL.search(activity))
