"""What every messenger is (sim/domain/channels.py) and everything that reads it: the task router,
the persona's capabilities, the plan check, the writing style and the «Подключить» menu."""
from __future__ import annotations

from typing import Any

import pytest

from sim.api.channel_logins import ChannelLogins
from sim.channels.instagram.login import InstagramLogins
from sim.channels.whatsapp.login import NOT_INSTALLED, WhatsAppLogins
from sim.config import Settings
from sim.db.models import Persona
from sim.domain.channels import (
    CHANNELS_QUEUE,
    SPECS,
    TELEGRAM_QUEUE,
    ChannelSpec,
    ReceiveMode,
    kind_by_url_name,
    spec_of,
)
from sim.domain.enums import ChannelKind, MediaKind, MessageKind
from sim.domain.writing import DEFAULT_MODE
from sim.services.capabilities import Capabilities, capabilities_of
from sim.services.plan_check import PlanError, validated_plan
from sim.services.writing import CHANNEL_STYLE, STYLE_HEADING, style_block
from sim.tasks.app import make_app
from sim.tasks.queue import (
    CHANNELS_CHAT_ACTION_TASK,
    CHANNELS_MARK_READ_TASK,
    CHANNELS_SEND_TASK,
    IG_POLL_TASK,
    IG_TICK_TASK,
    WA_CATCH_UP_TASK,
    ProcrastinateQueue,
)
from sim.telegram.login import TelegramLogins
from tests.fakes.channels import FakeEvolution, FakeIgFactory
from tests.services.factories import PERSONA_MODELS, agents_of, plan_json


class _Deferrer:
    def __init__(self, app: FakeApp, name: str, options: dict[str, Any]) -> None:
        self.app, self.name, self.options = app, name, options

    async def defer_async(self, **kwargs: Any) -> int:
        self.app.deferred.append((self.name, kwargs, self.options))
        return len(self.app.deferred)


class FakeApp:
    """Only what ProcrastinateQueue uses: configure_task(name, **options).defer_async()."""

    def __init__(self) -> None:
        self.deferred: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def configure_task(self, name: str, **options: Any) -> _Deferrer:
        return _Deferrer(self, name, options)


def test_every_kind_has_one_spec() -> None:
    assert set(SPECS) == set(ChannelKind)
    assert all(spec.kind == kind for kind, spec in SPECS.items())
    assert len({spec.url_name for spec in SPECS.values()}) == len(SPECS)
    telegram, instagram, whatsapp = (spec_of(k) for k in ChannelKind)
    assert (telegram.queue, telegram.namespace, telegram.receive) == (
        TELEGRAM_QUEUE, "telegram", ReceiveMode.PUSH)
    assert (instagram.queue, instagram.receive) == (CHANNELS_QUEUE, ReceiveMode.POLL)
    assert (whatsapp.queue, whatsapp.receive) == (CHANNELS_QUEUE, ReceiveMode.WEBHOOK)
    # Calls and round videos: Telegram only; Instagram's private API has no voice or typing.
    assert [s.calls for s in (telegram, instagram, whatsapp)] == [True, False, False]
    assert [s.can_send(MessageKind.VIDEO_NOTE) for s in (telegram, instagram, whatsapp)] == [
        True, False, False]
    assert instagram.sends == {MessageKind.TEXT, MessageKind.PHOTO} and not instagram.typing
    assert whatsapp.can_send(MessageKind.VOICE) and whatsapp.can_send(MessageKind.FILE)
    assert kind_by_url_name("telegram") == ChannelKind.TELEGRAM_USER
    assert kind_by_url_name("whatsapp") == ChannelKind.WHATSAPP
    assert kind_by_url_name("viber") is None


# --- the task router -----------------------------------------------------------------------


ROUTES = {1: ChannelKind.TELEGRAM_USER, 2: ChannelKind.INSTAGRAM, 3: ChannelKind.WHATSAPP}


class Router:
    """Chat 1 is Telegram, 2 Instagram, 3 WhatsApp; message N belongs to chat N."""

    async def for_chat(self, chat_id: int) -> ChannelSpec:
        return spec_of(ROUTES[chat_id])

    async def for_message(self, message_id: int) -> ChannelSpec:
        return spec_of(ROUTES[message_id])


async def test_sends_go_to_the_queue_of_the_chats_messenger() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app, router=Router())  # type: ignore[arg-type]
    await queue.send(11, chat_id=2)
    await queue.send(12, chat_id=1)
    await queue.send(3)                              # without a chat: asked by the message
    await queue.mark_read(2)
    await queue.chat_action(2, "typing", 40)         # Instagram has no typing: no job
    await queue.chat_action(3, "record_audio", 30)
    await queue.poll_instagram(7, 3.0)
    await queue.whatsapp_catch_up(9)
    assert [(name, options["queue"]) for name, _, options in app.deferred] == [
        (CHANNELS_SEND_TASK, "channels"), ("telegram:send_message", "telegram"),
        (CHANNELS_SEND_TASK, "channels"), (CHANNELS_MARK_READ_TASK, "channels"),
        (CHANNELS_CHAT_ACTION_TASK, "channels"), (IG_POLL_TASK, "channels"),
        (WA_CATCH_UP_TASK, "channels")]
    send = app.deferred[0]
    assert send[1] == {"message_id": 11}
    assert send[2] == {"queue": "channels", "lock": "send:2", "queueing_lock": "send-message:11"}
    poll = app.deferred[5]
    assert poll[2] == {"queue": "channels", "lock": "instagram:7",
                       "queueing_lock": "instagram-poll:7", "schedule_in": {"seconds": 3.0}}


async def test_without_a_router_everything_is_telegram() -> None:
    app = FakeApp()
    queue = ProcrastinateQueue(app)  # type: ignore[arg-type]
    await queue.send(1, chat_id=2)
    await queue.chat_action(2, "typing", 5)
    assert [name for name, _, _ in app.deferred] == ["telegram:send_message",
                                                     "telegram:chat_action"]


def test_every_process_knows_the_channels_tasks() -> None:
    app = make_app(Settings(env="test"))
    names = {CHANNELS_SEND_TASK, CHANNELS_MARK_READ_TASK, CHANNELS_CHAT_ACTION_TASK,
             IG_POLL_TASK, IG_TICK_TASK, WA_CATCH_UP_TASK}
    assert names <= set(app.tasks)
    assert {app.tasks[name].queue for name in names} == {"channels"}
    assert app.tasks[CHANNELS_SEND_TASK].retry_strategy is not None
    assert any(IG_TICK_TASK in str(key) for key in app.periodic_registry.periodic_tasks)


# --- what she may send ---------------------------------------------------------------------


def persona(**overrides: Any) -> Persona:
    return Persona(name="Марина", voice_enabled=True, photo_enabled=True, video_enabled=True,
                   voice_id="Vindemiatrix", agents=agents_of(PERSONA_MODELS), **overrides)


@pytest.mark.parametrize(("kind", "expected"), [
    (ChannelKind.TELEGRAM_USER, (True, True, True, True)),
    (ChannelKind.INSTAGRAM, (False, True, False, False)),
    (ChannelKind.WHATSAPP, (True, True, True, False)),
])
def test_capabilities_follow_the_messenger(kind: ChannelKind,
                                           expected: tuple[bool, ...]) -> None:
    caps = capabilities_of(persona(), kind)
    assert (caps.voice, caps.photo, caps.video, caps.video_note) == expected
    assert caps.photo_generate == caps.photo


def test_the_plan_check_refuses_what_the_messenger_can_not_show() -> None:
    whatsapp = Capabilities(voice=True, photo=True, photo_generate=True, video=True,
                            video_generate=True, video_note=False)
    note = plan_json({"type": "video_note", "media_id": 15})
    with pytest.raises(PlanError) as refused:
        validated_plan(note, catalog={15: MediaKind.VIDEO}, caps=whatsapp)
    assert any("кружок сейчас отправлять нельзя" in e for e in refused.value.errors)
    instagram = Capabilities(voice=False, photo=True, photo_generate=True, video=False,
                             video_generate=False, video_note=False)
    with pytest.raises(PlanError) as no_voice:
        validated_plan(plan_json({"type": "voice", "text": "привет"}), catalog={},
                       caps=instagram)
    assert any("голосовые сейчас нельзя" in e for e in no_voice.value.errors)


# --- how she writes there ------------------------------------------------------------------


def test_the_writing_style_has_a_block_per_messenger() -> None:
    assert set(CHANNEL_STYLE) == set(ChannelKind)
    telegram = style_block(DEFAULT_MODE)
    assert style_block(DEFAULT_MODE, channel=ChannelKind.TELEGRAM_USER) == telegram
    instagram = style_block(DEFAULT_MODE, channel=ChannelKind.INSTAGRAM)
    whatsapp = style_block(DEFAULT_MODE, channel=ChannelKind.WHATSAPP)
    assert instagram.startswith(STYLE_HEADING) and instagram.startswith(telegram)
    assert "директ Instagram" in instagram and "сторис" in instagram
    assert "Голосовых и кружков в директе нет" in instagram
    assert "голосовые — норма" in whatsapp and "записывает аудио" in whatsapp
    assert "Кружков в WhatsApp нет" in whatsapp
    # «Без отказов» stays the last line, after the messenger's manners.
    no_refusals = style_block(DEFAULT_MODE, no_refusals=True, channel=ChannelKind.WHATSAPP)
    assert no_refusals.index("Кружков в WhatsApp") < no_refusals.index("не отказываешь")


# --- the «Подключить» menu -------------------------------------------------------------------


def test_the_menu_says_why_a_messenger_is_not_available() -> None:
    settings = Settings(_env_file=None, env="test")
    bare = ChannelLogins.from_settings(settings, telegram=TelegramLogins(),
                                       instagram=InstagramLogins(FakeIgFactory()))
    views = {info.kind: info.view() for info in bare.kinds()}
    assert views[ChannelKind.WHATSAPP] == {"kind": "whatsapp", "label": "WhatsApp",
                                           "available": False, "reason": NOT_INSTALLED}
    assert views[ChannelKind.INSTAGRAM]["available"]
    ready = ChannelLogins(TelegramLogins(), InstagramLogins(FakeIgFactory()),
                          WhatsAppLogins(FakeEvolution(), "http://api:8000/api"),
                          instagram_ready=False)
    infos = {info.kind: info for info in ready.kinds()}
    assert infos[ChannelKind.WHATSAPP].available
    assert not infos[ChannelKind.INSTAGRAM].available and "instagrapi" in infos[
        ChannelKind.INSTAGRAM].reason
    assert Settings(_env_file=None, evolution_url="http://evolution:8080",
                    evolution_api_key="k").whatsapp_ready
    assert Settings(_env_file=None, public_url="https://sim.example").evolution_hooks_base == \
        "https://sim.example/api"
