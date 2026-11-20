"""Trusted schedule selection and real service wiring, using only fake transports/providers."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from sim.adapters.audio import run_process
from sim.adapters.call_ambience import CallAmbience
from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import VoiceAmbience
from sim.services.call_trace import CallTrace
from sim.services.persona_ambience import contextual_ambience, voice_ambience
from tests.test_call_ambience import RATE, noise
from tests.test_calls import FakeCall, FakeLive, FakeSession, chatting_world, service
from tests.test_voice import FakeEncoder, FakeSpeech, deliver, reply_to, voiced_world

NOW = datetime(2026, 11, 15, 12)  # Sunday 13:00 Berlin


@pytest.mark.parametrize("activity, expected", [
    ("обед дома", "room"), ("чай на кухне", "room"), ("сижу в комнате", "room"),
    ("обед в кафе", "cafe"), ("встреча в кофейне", "cafe"), ("кофейня", "cafe"),
    ("прогулка на улице", "street"), ("прогулка по улице", "street"),
    ("обед с мужем", "off"), ("пробежка в парке", "off"), ("рынок", "off"),
    ("работа", "off"), ("не дома", "off"), ("дома или в кафе", "off"),
    ("еду в кафе", "off"), ("возможно дома", "off"), ("после кафе дома", "off"),
    ("дома у окна, вид на улицу", "off"),
    ("созвон с Ингой в кафе", "off"), ("обсуждаю встречу в кафе", "off"),
    ("мечтаю о вечере дома", "off"), ("завтрак дома с мужем", "room"),
    ("онлайн-консультации с клиентами, из дома", "room"),
    ("консультации и переписка с клиентами, из дома", "room"),
    ("обед с Ингой в кафе возле дома", "cafe"),
    ("работаю из кофейни на углу, переписка", "cafe"),
    ("собираюсь работать из кофейни на углу, переписка", "off"),
])
def test_only_unambiguous_current_locations_select_background(activity, expected):
    assert contextual_ambience(f"вс 12:00-14:00 {activity}", "Europe/Berlin", NOW) == expected


def test_no_slot_bad_zone_invalid_line_or_conflicting_overlap_is_clean():
    for routine, tz in [("", "Europe/Berlin"), ("вс 01:00-02:00 дома", "Europe/Berlin"),
                        ("broken дома", "Europe/Berlin"), ("* 12:00-14:00 дома", "bad/zone"),
                        ("* 12:00-14:00 дома", ""),
                        ("* 12:00-14:00 дома\n* 12:00-14:00 в кафе", "Europe/Berlin"),
                        ("* 12:00-14:00 дома\n* 12:00-14:00 работа", "Europe/Berlin")]:
        assert contextual_ambience(routine, tz, NOW) == "off"


def test_timezone_date_override_midnight_and_dst_boundaries():
    routine = "* 12:00-14:00 дома\n2026-11-15 12:00-14:00 в кафе"
    assert contextual_ambience(routine, "Europe/Berlin", NOW) == "cafe"
    assert contextual_ambience(routine, "Asia/Tokyo", NOW) == "off"
    assert contextual_ambience("сб 23:00-02:00 дома", "UTC", datetime(2026, 11, 15, 1)) == "room"
    assert contextual_ambience("сб 23:00-02:00 дома", "UTC", datetime(2026, 11, 15, 2)) == "off"
    # Europe/Berlin repeats 02:30 twice when DST ends; both map to the same local activity.
    for hour in (0, 1):
        assert contextual_ambience("вс 02:00-03:00 дома", "Europe/Berlin",
                                   datetime(2026, 10, 25, hour, 30)) == "room"


@pytest.mark.parametrize("mode", ["off", "room", "street", "cafe", "random"])
def test_existing_explicit_note_settings_are_not_changed(mode):
    assert voice_ambience(mode, "* 12:00-14:00 дома", "UTC", NOW) == mode


async def test_call_plan_uses_shared_context_only_when_opted_in(container, world):
    await chatting_world(container, world)
    svc = service(container, FakeLive())
    svc.clock = lambda: NOW
    async with transaction(container.factory) as s:
        p = await s.get(Persona, world.persona.id)
        p.routine, p.timezone = "* 12:00-14:00 в кафе", "UTC"
        p.voice_ambience = VoiceAmbience.CONTEXTUAL
    assert (await svc.prepare(world.channel.id, "chat-1")).ambience == "cafe"
    for mode in (VoiceAmbience.OFF, VoiceAmbience.CAFE, VoiceAmbience.RANDOM):
        async with transaction(container.factory) as s:
            (await s.get(Persona, world.persona.id)).voice_ambience = mode
        assert (await svc.prepare(world.channel.id, "chat-1")).ambience == "off"


async def test_background_continues_in_pauses_without_claiming_model_speech(container, caplog):
    call, session = FakeCall(frames=3), FakeSession()
    call.rate_hz = RATE
    trace = CallTrace(True, 1)
    svc = service(container, FakeLive(session))
    await svc.converse(call, session, trace, CallAmbience(noise(), RATE))
    assert call.played and all(len(f) == RATE // 100 * 2 for f in call.played)
    assert trace.counters["ambience_frames_out"] > 0
    assert trace.counters["frames_out"] == 0
    assert "first_frame_sent" not in trace.seen


async def test_hangup_during_background_preparation_closes_the_session_unanswered(
        container, world, monkeypatch):
    await chatting_world(container, world)
    live, call = FakeLive(), FakeCall()

    async def prepare(*args):
        call.ended = True
        return None

    monkeypatch.setattr("sim.services.calls.prepare_call_ambience", prepare)
    result = await service(container, live).handle(world.channel.id, call)
    # The session opens during the ring pause (it hides its latency); a caller who is gone
    # by then is never picked up and the session is closed at once.
    assert result.status == "skipped:hung_up" and not call.answered
    assert len(live.opened) == 1 and live.session.finished == 1


async def test_notes_resolve_at_send_time_not_queue_time(container, world, connector):
    encoder = FakeEncoder()
    await voiced_world(container, world, connector, speech=FakeSpeech(), encoder=encoder)
    queued = await reply_to(container, world, client_voice=True)
    # An edit while queued takes effect at actual render time; no new model decision.
    async with transaction(container.factory) as s:
        p = await s.get(Persona, world.persona.id)
        p.voice_ambience = VoiceAmbience.CONTEXTUAL
        p.routine, p.timezone = "* 00:00-24:00 в кафе", "UTC"
    await deliver(container, queued.id)
    assert encoder.ambiences == ["cafe"]


async def test_preview_uses_same_contextual_selection(container):
    from sim.services.preview_reply import preview_voice

    seen = []

    async def render(*args, **kwargs):
        seen.append(kwargs["ambience"])
        return SimpleNamespace(media_url="fake.ogg", duration_s=1)

    container.clock = lambda: NOW
    container.voice_renderer = lambda: SimpleNamespace(render=render)
    persona = SimpleNamespace(voice_mode="always", voice_id="Kore", voice_style="",
                              voice_ambience="contextual", routine="* 12:00-14:00 дома", timezone="UTC")
    await preview_voice(container, persona, "Привет", False)
    assert seen == ["room"]


async def test_cancelled_audio_render_kills_and_reaps_child(monkeypatch):
    state = []

    async def communicate(data=None):
        if data is not None:
            raise asyncio.CancelledError
        state.append("drained")
        return b"", b""

    process = SimpleNamespace(communicate=communicate, returncode=None,
                              kill=lambda: state.append("kill"))

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(asyncio.CancelledError):
        await run_process(("ffmpeg",), b"", 1)
    assert state == ["kill", "drained"]


async def test_contextual_setting_roundtrips_api_without_changing_other_personas(api):
    from tests.test_api_smoke import owner

    headers = await owner(api)
    first = (await api.post("/api/personas", json={"agent_prompt": "Ты тест.", "name": "Марина"}, headers=headers)).json()
    other = (await api.post("/api/personas", json={"agent_prompt": "Ты тест.", "name": "Ева"}, headers=headers)).json()
    result = await api.put(f"/api/personas/{first['id']}",
                           json={"agent_prompt": "Ты тест.", "name": "Марина", "voice_ambience": "contextual"}, headers=headers)
    assert result.status_code == 200 and result.json()["voice_ambience"] == "contextual"
    fetched = (await api.get(f"/api/personas/{other['id']}", headers=headers)).json()
    assert fetched["voice_ambience"] == "off"
