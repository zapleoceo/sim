"""Persona phone calls: PCM helpers, call prompt, the call service on a fake call and a fake live
voice, the Broker /v1/live client on a fake socket, the py-tgcalls bridge on a fake library, and
the wiring (connector, transport, worker, persona API, migration). Nothing touches Telegram or
the Broker."""
from __future__ import annotations

import asyncio
import json
from array import array
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from sim.adapters.channels.capabilities import CallListener, Capability
from sim.adapters.channels.telegram_user import TelegramUserAdapter, TelethonTransport, TGUserState
from sim.adapters.channels.tg_calls import TelegramCallBridge, has_status
from sim.adapters.db.models import Dialog, Message, Persona
from sim.adapters.db.session import transaction
from sim.adapters.llm.broker_live import BrokerLiveVoice, live_url
from sim.adapters.pcm import PcmBuffer, frame_bytes, resample
from sim.connectors.telegram_user import TelegramUserConnector
from sim.domain.enums import Author, ClientStatus, Direction, Stage
from sim.ports.live_voice import (
    AUDIO,
    INTERRUPTED,
    TRANSCRIPT,
    TURN_COMPLETE,
    LiveVoiceError,
    LiveVoiceEvent,
    LiveVoiceSummary,
)
from sim.services.call_prompt import CALL_RULES, GREETING, call_system_prompt, chat_transcript
from sim.services.calls import CALL_KIND, CallService, clock_text, language_code
from sim.services.prompt import DialogContext, PersonaProfile, StrategyProfile, Turn
from sim.worker.loop import Worker
from tests.conftest import inbound
from tests.test_ingest import ingest


def pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


# ---- PCM ------------------------------------------------------------------------------------

def test_resample_keeps_equal_rates_and_drops_a_torn_sample():
    assert resample(pcm(1, 2) + b"\x07", 16000, 16000) == pcm(1, 2)
    assert resample(b"", 48000, 16000) == b""


def test_downsampling_by_an_integer_factor_averages_each_group():
    assert resample(pcm(3, 6, 9, 30, 30, 30), 48000, 16000) == pcm(6, 30)


def test_upsampling_interpolates_linearly():
    assert resample(pcm(0, 100), 24000, 48000) == pcm(0, 50, 100, 100)
    assert len(resample(pcm(*range(240)), 24000, 48000)) == frame_bytes(48000)   # 10 ms


def test_buffer_hands_out_whole_frames_and_pads_the_tail():
    buf = PcmBuffer(4)
    buf.push(b"abcdef")
    assert buf.pop_frame() == b"abcd" and buf.pop_frame() is None and len(buf) == 2
    assert buf.flush_tail() == b"ef\x00\x00" and buf.flush_tail() is None
    buf.push(b"xyz")
    buf.clear()
    assert len(buf) == 0
    assert frame_bytes(48000) == 960 and frame_bytes(16000, 40) == 1280


# ---- call prompt ----------------------------------------------------------------------------

def ctx() -> DialogContext:
    return DialogContext(stage=Stage.ENGAGED, status="active", client_name="Анна",
                         channel_kind="telegram_user", ref_code="abcdefgh",
                         now=datetime(2026, 11, 13, 12, 0))


def test_call_prompt_is_persona_strategy_rules_chat_and_context_without_json():
    prompt = call_system_prompt(
        PersonaProfile(name="Марина", description="Ты Марина, 45 лет."),
        StrategyProfile(name="S", goal="Регистрация", registration_url="https://x/?ref={ref}"),
        [Turn("user", "Привет"), Turn("assistant", "Привет! Как дела?"), Turn("user", "норм")],
        ctx(),
    )
    assert prompt.startswith("Ты Марина, 45 лет.")
    assert CALL_RULES in prompt and "ФОРМАТ ОТВЕТА" not in prompt
    assert "Клиент: Привет\nТы: Привет! Как дела?\nКлиент: норм" in prompt
    assert "https://x/?ref=abcdefgh" in prompt and "СТРАТЕГИЯ" in prompt
    assert prompt.index(CALL_RULES) < prompt.index("ПЕРЕПИСКА") < prompt.index("КОНТЕКСТ")


def test_chat_transcript_is_flat_capped_and_empty_without_history():
    assert chat_transcript([]) == ""
    long = chat_transcript([Turn("user", "a\nb " + "x" * 2000)])
    assert long.startswith("Клиент: a b ") and len(long) <= len("Клиент: ") + 1500


def test_small_helpers():
    assert (language_code(" RU "), language_code("uk"), language_code("русский")) == \
        ("ru-RU", "uk-UA", None)
    assert (clock_text(151.4), clock_text(None), clock_text(9)) == ("2:31", "0:00", "0:09")
    assert has_status(0b110, 0b010) and not has_status(0b100, 0b010)
    assert has_status("x", "x") and not has_status("x", "y")


# ---- call service ---------------------------------------------------------------------------

RATE = 48000
FRAME = b"\x01\x00" * (RATE // 100)          # 10 ms at 48 kHz


class FakeCall:
    def __init__(self, thread: str = "chat-1", frames: int = 6, gap: float = 0.02,
                 fail_answer: bool = False) -> None:
        self.external_thread_id = thread
        self.rate_hz = RATE
        self.n = frames
        self.gap = gap
        self.fail_answer = fail_answer
        self.answered = False
        self.hung_up = False
        self.played: list[bytes] = []

    async def answer(self) -> None:
        if self.fail_answer:
            raise RuntimeError("no call")
        self.answered = True

    async def frames(self):
        for _ in range(self.n):
            await asyncio.sleep(self.gap)
            yield FRAME

    async def play(self, data: bytes) -> None:
        self.played.append(data)

    async def hangup(self) -> None:
        self.hung_up = True


class FakeSession:
    input_rate_hz = 16000
    output_rate_hz = 24000

    def __init__(self, events=(), summary: LiveVoiceSummary | None = None, *,
                 end_after_events: bool = False) -> None:
        self._events = list(events)
        self.end_after_events = end_after_events
        self.sent: list[bytes] = []
        self.finished = 0
        self.summary = summary or LiveVoiceSummary(
            transcript=[("user", "Алло, это Марина?"), ("assistant", "Да, привет! Слушаю.")],
            duration_s=151.0, cost_usd=0.05, model="gemini/gemini-3.8-live",
            tokens_in=900, tokens_out=400, end_reason="caller_hung_up")

    async def send_audio(self, data: bytes) -> None:
        self.sent.append(data)

    async def events(self):
        for event in self._events:
            yield event
        if not self.end_after_events:
            await asyncio.Event().wait()

    async def finish(self) -> LiveVoiceSummary:
        self.finished += 1
        return self.summary


class FakeLive:
    def __init__(self, session: FakeSession | None = None, error: Exception | None = None) -> None:
        self.session = session or FakeSession()
        self.error = error
        self.opened: list[dict] = []

    async def open(self, **kw):
        self.opened.append(kw)
        if self.error is not None:
            raise self.error
        return self.session


async def chatting_world(container, world, *, accept=True, voice="Kore") -> int:
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.accept_calls, persona.voice_id = accept, voice
    await ingest(container, world.channel.id, inbound("Добрый день, расскажите про курс"))
    async with container.factory() as s:
        return (await s.scalars(select(Dialog))).unique().one().id


def service(container, live) -> CallService:
    container.live_voice = live
    return container.calls()


async def dialog_messages(container, dialog_id) -> list[Message]:
    async with container.factory() as s:
        return list(await s.scalars(
            select(Message).where(Message.dialog_id == dialog_id).order_by(Message.id)))


async def test_answers_a_known_client_and_writes_the_transcript(container, world):
    dialog_id = await chatting_world(container, world)
    session = FakeSession([LiveVoiceEvent(AUDIO, pcm=b"\x02\x00" * 240),
                           LiveVoiceEvent(TURN_COMPLETE),
                           LiveVoiceEvent(TRANSCRIPT, role="user", text="ignored live")])
    live = FakeLive(session)
    call = FakeCall()
    outcome = await service(container, live).handle(world.channel.id, call)
    assert outcome.status == "answered" and call.answered and call.hung_up
    assert session.finished == 1
    opened = live.opened[0]
    assert (opened["voice"], opened["language"], opened["greeting"], opened["workflow"]) == \
        ("Kore", "ru-RU", GREETING, "call")
    assert CALL_RULES in opened["system"] and "Добрый день, расскажите про курс" in opened["system"]
    assert session.sent and all(len(chunk) >= frame_bytes(16000, 40) for chunk in session.sent)
    assert call.played and all(len(f) == frame_bytes(RATE) for f in call.played)
    rows = await dialog_messages(container, dialog_id)
    header, said, answer = rows[-3:]
    assert (header.media_kind, header.text, header.direction) == \
        (CALL_KIND, "Входящий звонок · 2:31", Direction.IN)
    assert header.cost_usd == 0.05 and header.model == "gemini/gemini-3.8-live"
    assert (said.author, said.text) == (Author.CLIENT, "Алло, это Марина?")
    assert (answer.author, answer.direction, answer.text) == \
        (Author.AI, Direction.OUT, "Да, привет! Слушаю.")
    assert outcome.message_ids == [header.id, said.id, answer.id]
    async with container.factory() as s:
        dialog = await s.get(Dialog, dialog_id)
        assert dialog.last_message_text == "Да, привет! Слушаю."


async def test_the_caller_talking_over_her_drops_the_buffered_speech(container, world):
    await chatting_world(container, world)
    session = FakeSession([LiveVoiceEvent(AUDIO, pcm=b"\x02\x00" * 24000),   # 1 s of speech
                           LiveVoiceEvent(INTERRUPTED)])
    call = FakeCall(frames=3)
    await service(container, FakeLive(session)).handle(world.channel.id, call)
    assert len(call.played) < 20      # far less than the 100 frames of that second


async def test_when_the_broker_ends_her_last_words_still_play(container, world):
    await chatting_world(container, world)
    session = FakeSession([LiveVoiceEvent(AUDIO, pcm=b"\x02\x00" * 2400)],   # 100 ms
                          end_after_events=True)
    call = FakeCall(frames=1000, gap=0.05)
    await service(container, FakeLive(session)).handle(world.channel.id, call)
    assert len(call.played) == 10 and call.hung_up


@pytest.mark.parametrize("setup, reason", [
    ({"accept": False}, "calls_off"),
    ({"voice": ""}, "no_voice"),
])
async def test_persona_settings_decide_whether_she_answers(container, world, setup, reason):
    await chatting_world(container, world, **setup)
    live, call = FakeLive(), FakeCall()
    outcome = await service(container, live).handle(world.channel.id, call)
    assert outcome.status == f"skipped:{reason}"
    assert not live.opened and not call.answered


async def test_unknown_callers_and_operator_dialogs_are_left_ringing(container, world):
    dialog_id = await chatting_world(container, world)
    stranger = await service(container, FakeLive()).handle(world.channel.id, FakeCall("other"))
    assert stranger.status == "skipped:unknown_caller"
    async with transaction(container.factory) as s:
        (await s.get(Dialog, dialog_id)).ai_paused = True
    taken = await service(container, FakeLive()).handle(world.channel.id, FakeCall())
    assert taken.status == "skipped:operator_dialog"
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, dialog_id)
        dialog.ai_paused = False
        dialog.client.status = ClientStatus.REFUSED
    refused = await service(container, FakeLive()).handle(world.channel.id, FakeCall())
    assert refused.status == "skipped:operator_dialog"
    assert (await service(container, None).handle(world.channel.id, FakeCall())).status == \
        "skipped:no_live_voice"
    assert (await service(container, FakeLive()).handle(999, FakeCall())).status == \
        "skipped:no_channel"


async def test_a_broker_refusal_never_picks_up(container, world):
    await chatting_world(container, world)
    call = FakeCall()
    outcome = await service(container, FakeLive(error=LiveVoiceError("503"))).handle(
        world.channel.id, call)
    assert outcome.status == "refused" and not call.answered


async def test_a_failed_answer_closes_the_session(container, world):
    dialog_id = await chatting_world(container, world)
    session = FakeSession()
    outcome = await service(container, FakeLive(session)).handle(
        world.channel.id, FakeCall(fail_answer=True))
    assert outcome.status == "failed" and session.finished == 1
    assert all(m.media_kind != CALL_KIND for m in await dialog_messages(container, dialog_id))


async def test_a_call_without_words_is_still_recorded(container, world):
    dialog_id = await chatting_world(container, world)
    session = FakeSession(summary=LiveVoiceSummary(duration_s=4))
    await service(container, FakeLive(session)).handle(world.channel.id, FakeCall(frames=1))
    last = (await dialog_messages(container, dialog_id))[-1]
    assert (last.media_kind, last.text) == (CALL_KIND, "Входящий звонок · 0:04")


# ---- Broker /v1/live client -----------------------------------------------------------------

class FakeSocket:
    def __init__(self, first, stream=()) -> None:
        self.first = first
        self.stream = list(stream)
        self.sent: list = []
        self.closed = False
        self.more = asyncio.Event()

    async def send(self, data) -> None:
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data).get("type") == "end":
            self.more.set()

    async def recv(self):
        if isinstance(self.first, Exception):
            raise self.first
        return json.dumps(self.first)

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for item in self.stream:
            if item == "WAIT_END":
                await self.more.wait()
                continue
            if isinstance(item, Exception):
                raise item
            yield item if isinstance(item, bytes | str) else json.dumps(item)

    async def close(self) -> None:
        self.closed = True


def broker(ws) -> tuple[BrokerLiveVoice, dict]:
    seen: dict = {}

    async def connect(url, headers):
        seen.update(url=url, headers=headers)
        if isinstance(ws, Exception):
            raise ws
        return ws
    return BrokerLiveVoice("https://aib.example.com/", "key-1", connect=connect), seen


def test_live_url():
    assert live_url("https://aib.zapleo.com/") == "wss://aib.zapleo.com/v1/live"
    assert live_url("http://localhost:8000") == "ws://localhost:8000/v1/live"
    assert live_url("ws://x") == "ws://x/v1/live"


async def test_broker_session_streams_events_and_returns_the_summary():
    ws = FakeSocket({"type": "ready", "input_rate_hz": 16000, "output_rate_hz": 24000}, [
        b"PCM", {"type": "transcript", "role": "user", "text": "Алло"}, {"type": "interrupted"},
        {"type": "turn_complete"}, {"type": "pong"}, "[1]", "WAIT_END",
        {"type": "done", "transcript": [{"role": "user", "text": "Алло"},
                                         {"role": "assistant", "text": " "}, "junk"],
         "duration_s": 12.5, "cost_usd": 0.01, "model": "gemini/gemini-3.8-live",
         "tokens_in": 100, "tokens_out": 50, "end_reason": "caller_ended"},
    ])
    client, seen = broker(ws)
    session = await client.open(voice="Kore", system="Ты Марина", language="ru-RU",
                                greeting="поздоровайся", workflow="call", max_seconds=300)
    assert seen == {"url": "wss://aib.example.com/v1/live", "headers": {"X-Project-Key": "key-1"}}
    start = json.loads(ws.sent[0])
    assert start == {"type": "start", "voice": "Kore", "system": "Ты Марина", "language": "ru-RU",
                     "greeting": "поздоровайся", "workflow": "call", "max_seconds": 300}
    await session.send_audio(b"IN")
    await session.send_audio(b"")
    events = []
    async for event in session.events():
        events.append(event)
        if len(events) == 4:
            break
    assert [e.kind for e in events] == [AUDIO, TRANSCRIPT, INTERRUPTED, TURN_COMPLETE]
    assert events[0].pcm == b"PCM" and (events[1].role, events[1].text) == ("user", "Алло")
    summary = await session.finish()
    assert summary.transcript == [("user", "Алло")]
    assert (summary.duration_s, summary.cost_usd, summary.tokens_in, summary.end_reason) == \
        (12.5, 0.01, 100, "caller_ended")
    assert ws.sent[1] == b"IN" and json.loads(ws.sent[2]) == {"type": "end"} and ws.closed
    assert await session.finish() is summary          # idempotent
    await session.send_audio(b"late")                 # ignored after the end
    assert b"late" not in ws.sent
    assert [e async for e in session.events()] == []


async def test_a_dropped_broker_socket_ends_the_session():
    ws = FakeSocket({"type": "ready"}, [ConnectionError("gone")])
    session = await broker(ws)[0].open(voice="Kore", system="")
    assert [e async for e in session.events()] == []
    assert (await session.finish()).end_reason == "no_summary"
    assert (session.input_rate_hz, session.output_rate_hz) == (16000, 24000)


@pytest.mark.parametrize("ws, match", [
    (FakeSocket({"type": "error", "code": 503, "detail": "no paid gemini key"}), "503"),
    (FakeSocket(["not a dict"]), "refused"),
    (FakeSocket(TimeoutError()), "no answer"),
    (OSError("refused"), "connect failed"),
])
async def test_broker_refusals_are_live_voice_errors(ws, match):
    with pytest.raises(LiveVoiceError, match=match):
        await broker(ws)[0].open(voice="Kore", system="")
    if isinstance(ws, FakeSocket):
        assert ws.closed


# ---- py-tgcalls bridge ----------------------------------------------------------------------

class Status:
    INCOMING_CALL = 1
    DISCARDED_CALL = 2
    LEFT_CALL = 4
    BUSY_CALL = 8


class FakeChatUpdate:
    Status = Status

    def __init__(self, chat_id, status) -> None:
        self.chat_id, self.status = chat_id, status


class FakeFrames:
    def __init__(self, chat_id, frames, direction="in", device="mic") -> None:
        self.chat_id, self.direction, self.device = chat_id, direction, device
        self.frames = [SimpleNamespace(frame=f) for f in frames]


class FakeTgCalls:
    def __init__(self, client) -> None:
        self.client = client
        self.handler = None
        self.log: list = []

    def on_update(self, filters=None):
        def register(func):
            self.handler = func
            return func
        return register

    async def start(self) -> None:
        self.log.append("start")

    async def play(self, chat_id, stream) -> None:
        self.log.append(("play", chat_id, stream))

    async def record(self, chat_id, stream) -> None:
        self.log.append(("record", chat_id, stream))

    async def send_frame(self, chat_id, device, data) -> None:
        self.log.append(("frame", chat_id, device, data))

    async def leave_call(self, chat_id) -> None:
        self.log.append(("leave", chat_id))


LIB = SimpleNamespace(
    PyTgCalls=FakeTgCalls, ChatUpdate=FakeChatUpdate, StreamFrames=FakeFrames,
    Device=SimpleNamespace(MICROPHONE="mic"), Direction=SimpleNamespace(INCOMING="in"),
    ExternalMedia=SimpleNamespace(AUDIO="ext-audio"),
    MediaStream=lambda kind, params: ("media", kind, params),
    RecordStream=lambda audio, params: ("record", audio, params),
    AudioParameters=lambda rate, channels: (rate, channels),
)


async def test_bridge_answers_streams_and_hangs_up():
    got: list = []
    release = asyncio.Event()

    async def on_call(call):
        await call.answer()
        await call.play(b"OUT")
        async for frame in call.frames():
            got.append(frame)
        release.set()

    bridge = TelegramCallBridge("client", on_call, lib=LIB)
    await bridge.start()
    await bridge.start()                                  # once
    calls = bridge.calls
    assert calls.log == ["start"] and calls.handler == bridge.on_update
    await bridge.on_update(None, FakeChatUpdate(42, Status.INCOMING_CALL))
    await bridge.on_update(None, FakeChatUpdate(42, Status.INCOMING_CALL))   # duplicate ring
    await asyncio.sleep(0)
    await bridge.on_update(None, FakeFrames(42, [b"A", b"B"]))
    await bridge.on_update(None, FakeFrames(42, [b"X"], direction="out"))     # her own audio
    await bridge.on_update(None, FakeFrames(7, [b"Z"]))                       # no such call
    await bridge.on_update(None, "something else")
    await bridge.on_update(None, FakeChatUpdate(42, Status.DISCARDED_CALL))
    await asyncio.wait_for(release.wait(), 1)
    await asyncio.gather(*bridge.tasks)
    assert got == [b"A", b"B"]
    assert ("play", 42, ("media", "ext-audio", (48000, 1))) in calls.log
    assert ("record", 42, ("record", True, (48000, 1))) in calls.log
    assert ("frame", 42, "mic", b"OUT") in calls.log
    assert ("leave", 42) not in calls.log                 # the caller hung up first
    assert bridge.active == {}


async def test_bridge_hangs_up_an_answered_call_and_leaves_an_unanswered_one_ringing():
    async def talk(call):
        await call.answer()

    async def ignore(call):
        return None

    async def crash(call):
        raise RuntimeError("boom")

    for handler, left in ((talk, True), (ignore, False), (crash, False)):
        bridge = TelegramCallBridge("client", handler, lib=LIB)
        await bridge.on_update(None, FakeChatUpdate(5, Status.INCOMING_CALL))
        await asyncio.gather(*bridge.tasks)
        assert (("leave", 5) in bridge.calls.log) is left
        assert bridge.active == {}


# ---- wiring ---------------------------------------------------------------------------------

class FakeTelethonClient:
    def __init__(self) -> None:
        self.connected = False
        self.handlers: list = []

    def add_event_handler(self, handler, event) -> None:
        self.handlers.append(handler)

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True


async def test_transport_attaches_the_call_bridge_once_and_survives_its_failure():
    made: list = []

    class Bridge:
        def __init__(self, client, on_call) -> None:
            made.append((client, on_call))

        async def start(self) -> None:
            return None

    async def on_call(call):
        return None

    client = FakeTelethonClient()
    transport = TelethonTransport(lambda: client, lambda: None, on_call=on_call, call_bridge=Bridge)
    assert await transport.ready() is client
    await transport.ready()
    assert len(made) == 1 and made[0] == (client, on_call) and transport.calls is not None

    class Broken:
        def __init__(self, client, on_call) -> None:
            raise ImportError("no ntgcalls")

    broken = TelethonTransport(FakeTelethonClient, on_call=on_call, call_bridge=Broken)
    await broken.ready()
    await broken.ready()
    assert broken.calls is None
    plain = TelethonTransport(FakeTelethonClient, call_bridge=Broken)
    await plain.ready()
    assert plain.calls is None


async def test_connector_routes_calls_of_its_channel_to_the_worker_handler():
    seen: list = []

    async def handler(channel_id, call):
        seen.append((channel_id, call))

    conn = TelegramUserConnector(client_factory=lambda *a, **kw: FakeTelethonClient())
    assert isinstance(conn, CallListener) and Capability.ACCEPT_CALL in conn.capabilities
    conn.listen(lambda: None)
    conn.listen_calls(handler)
    port = conn.build_port(9, {"string_session": "s", "api_id": 1, "api_hash": "h"})
    assert isinstance(port, TelegramUserAdapter) and Capability.ACCEPT_CALL in port.capabilities
    await port.t._on_call("CALL")
    assert seen == [(9, "CALL")]
    other = TelegramUserConnector(client_factory=lambda *a, **kw: FakeTelethonClient())
    other.listen_calls(handler)              # an API process never listens: no call bridge
    assert other.build_port(1, {"string_session": "s", "api_id": 1, "api_hash": "h"}).t._on_call \
        is None
    assert TGUserState().cursor == {}


async def test_worker_registers_for_calls_and_answers_them(container, monkeypatch):
    registered: list = []

    class Listener:
        polling = False

        def listen_calls(self, handler) -> None:
            registered.append(handler)

    container.registry = {**container.registry, "calls": Listener()}
    worker = Worker(container)
    assert registered == [worker.answer_call]

    class Calls:
        def __init__(self, error=None) -> None:
            self.error = error
            self.seen: list = []

        async def handle(self, channel_id, call):
            self.seen.append((channel_id, call))
            if self.error:
                raise self.error
            return SimpleNamespace(status="answered")

    ok = Calls()
    monkeypatch.setattr(container, "calls", lambda: ok)
    await worker.answer_call(3, "CALL")
    assert ok.seen == [(3, "CALL")]
    monkeypatch.setattr(container, "calls", lambda: Calls(RuntimeError("boom")))
    await worker.answer_call(3, "CALL")      # logged, never raised into the call bridge

    registered.clear()
    container.settings.calls_enabled = False
    Worker(container)
    assert registered == []


async def test_persona_api_round_trips_accept_calls_and_needs_a_voice(api):
    from tests.test_api_smoke import owner

    h = await owner(api)
    created = (await api.post("/api/personas", json={"name": "Марина"}, headers=h)).json()
    assert created["accept_calls"] is False
    body = {"name": "Марина", "voice_id": "Kore", "accept_calls": True}
    assert (await api.put(f"/api/personas/{created['id']}", json=body,
                          headers=h)).json()["accept_calls"] is True
    resp = await api.put(f"/api/personas/{created['id']}",
                         json={"name": "Марина", "accept_calls": True}, headers=h)
    assert resp.status_code == 422
