"""Conservative sound selection from owner-authored current routine, never chat/LLM text.

Unknown, ambiguous, negated and transitional locations are silent. A routine is a fictional
persona schedule, not sensor evidence. No history, biography or client assertions can set it.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sim.domain.enums import VoiceAmbience
from sim.services.persona_clock import local_time
from sim.services.persona_routine import doing_now

# Only explicit locations. Eating, working, sleeping and running alone say nothing about
# the acoustic scene. A park is NOT traffic; do not map it to street.
# Full simple slot phrases, NOT keyword search in arbitrary prose. This avoids treating
# "calling Inga in a cafe" or "thinking about home" as the persona's own location.
COMPANY = r"(?: с (?:мужем|мамой|Ингой|подругой Ингой))?"
LOCATIONS = {
    VoiceAmbience.ROOM: re.compile(
        r"(?:я |сижу |обед |ужин |завтрак |чай |отдыхаю |читаю |готовлю |работаю )?"
        r"(?:дома|на кухне|в квартире|в комнате|у мамы дома)" + COMPANY, re.I),
    VoiceAmbience.STREET: re.compile(
        r"(?:я |прогулка |гуляю |прогулка пешком )?(?:на улице|по улице|вдоль улицы)" + COMPANY, re.I),
    VoiceAmbience.CAFE: re.compile(
        r"(?:я |сижу |обед |завтрак |чай |кофе |встреча )?"
        r"(?:в кафе|в кофейне|кофейня)" + COMPANY, re.I),
}


def contextual_ambience(routine: str, timezone: str, now: datetime) -> VoiceAmbience:
    """All active slots must explicitly agree; unknown overlap means silence.

    Uses routine date overrides/midnight/DST; invalid time zones cannot silently become Kyiv.
    """
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return VoiceAmbience.OFF
    activities = doing_now(routine, local_time(timezone, now))
    if not activities:
        return VoiceAmbience.OFF
    choices: set[VoiceAmbience] = set()
    for activity in activities:
        phrase = " ".join(activity.strip(" .!").split())
        matches = {preset for preset, pattern in LOCATIONS.items() if pattern.fullmatch(phrase)}
        if len(matches) != 1:
            return VoiceAmbience.OFF
        choices.update(matches)
    return next(iter(choices)) if len(choices) == 1 else VoiceAmbience.OFF


def voice_ambience(mode: str, routine: str, timezone: str, now: datetime) -> str:
    """Notes retain explicit presets; contextual mode uses the shared rule."""
    if mode == VoiceAmbience.CONTEXTUAL:
        return contextual_ambience(routine, timezone, now)
    return mode
