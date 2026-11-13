"""The role game (docs/game.md): the command vocabulary, the consent screen, who answers a dialog,
the exits, the wipe, and who picks up a call."""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Client, Dialog, Job, Message, Persona, Strategy
from sim.adapters.db.session import transaction
from sim.domain.enums import ChannelKind, VoiceMode
from sim.domain.game import (
    CONSENT_PROMPT,
    MINOR_NOTICE,
    RESTART_NOTICE,
    SAFETY_NOTICE,
    STOP_WORD,
    GameState,
    Move,
    in_distress,
    is_minor,
    next_move,
    normalize,
    stated_age,
    vocabulary_text,
)
from sim.persona_prompts import GAME_STRATEGY_NAME, SIM1
from sim.seed import seed
from sim.services import jobkinds
from sim.services.app_settings import AppSettingsService
from sim.services.game import CHANNEL_KEY, OWNER_KEY, GameService
from tests.conftest import OWNER, decision_json, inbound, login
from tests.test_calls import FakeCall, FakeLive, chatting_world, service
from tests.test_ingest import ingest

OWNER_TG = 169510539
_ext = itertools.count(1)

IDLE, ASK, ON, OVER, MINOR = (GameState.IDLE, GameState.ASK, GameState.ON, GameState.OVER,
                              GameState.MINOR)


# ---- the vocabulary: pure rules -----------------------------------------------------------
def test_normalize_keeps_only_words():
    assert normalize("  НИ!!! ") == "ни"
    assert normalize("Начни с начала…") == "начни с начала"
    assert normalize("Ещё, мне что-то плохо 🙁") == "еще мне что то плохо"
    assert normalize(None) == ""


@pytest.mark.parametrize("state, texts, consented, move", [
    # starting: a stranger sees the consent screen first, a consented player (the owner) does not
    (IDLE, ["НИ"], False, Move.ASK),
    (IDLE, ["начать игру"], False, Move.ASK),
    (IDLE, ["ни"], True, Move.START),
    (OVER, ["Ни."], True, Move.START),
    (IDLE, ["ни один не пришёл"], True, None),            # a command is a whole message
    (IDLE, ["начать игру в шахматы?"], False, None),
    (ON, ["ни"], True, None),                              # already playing: plain talk
    # the consent screen: only "да" starts the game
    (ASK, ["Да"], False, Move.START),
    (ASK, ["да!"], False, Move.START),
    (ASK, ["да, давай"], False, Move.DECLINE),
    (ASK, ["а что это?"], False, Move.DECLINE),
    (ASK, ["да", "хотя нет"], False, Move.DECLINE),        # the last message decides
    (ASK, ["НИ"], False, Move.ASK),                        # asked again: the screen again
    (IDLE, ["да"], False, None),                           # "да" means nothing without the screen
    # leaving: the stop word anywhere, in any form, only matters while playing
    (ON, ["кикимора"], True, Move.STOP),
    (ON, ["ну ты и Кикимора, конечно!"], True, Move.STOP),
    (ON, ["привет", "скажи кикимору", "ок"], True, Move.STOP),
    (IDLE, ["кикимора болотная"], False, None),
    (OVER, ["кикимора"], True, None),
    # the safeguard and the age rule beat everything else
    (ON, ["мне что-то плохо"], True, Move.SAFETY),
    (ON, ["я запутался, где игра, а где реальность"], True, Move.SAFETY),
    (ON, ["мені погано"], True, Move.SAFETY),
    (ON, ["мне нужна настоящая помощь, кикимора"], True, Move.SAFETY),
    (ON, ["выйди из роли"], True, Move.SAFETY),
    (ON, ["мне 15 лет"], True, Move.MINOR),
    (ON, ["мне плохо, мне 15"], True, Move.MINOR),
    (ON, ["я в 9 классе учусь"], True, Move.MINOR),
    (OVER, ["мне плохо"], True, None),                     # out of the game the persona just talks
    # after an under-age exit the game never starts again
    (MINOR, ["ни"], True, None),
    (MINOR, ["начать игру"], False, None),
    # the wipe works in every state; the latest whole-message command wins
    (IDLE, ["НСН"], False, Move.RESTART),
    (ON, ["начни сначала"], True, Move.RESTART),
    (MINOR, ["нсн"], False, Move.RESTART),
    (OVER, ["нсн", "ни"], True, Move.START),
    (OVER, ["ни", "нсн"], True, Move.RESTART),
    (ON, ["как дела?"], True, None),
    (ON, [], True, None),
])
def test_next_move(state, texts, consented, move):
    assert next_move(state, texts, consented=consented) is move


@pytest.mark.parametrize("text, age", [
    ("мне 15 лет", 15), ("мне уже 17", 17), ("мені шістнадцять років", 16),
    ("да мне пятнадцать", 15), ("мне 35 лет", 35), ("мне 18", 18),
    ("дай мне 5 минут", None), ("мне 5 раз звонили", None), ("обычный день", None),
])
def test_stated_age(text, age):
    assert stated_age(normalize(text)) == age


@pytest.mark.parametrize("text, minor", [
    ("мне 15 лет", True), ("мне уже 17", True), ("мені шістнадцять років", True),
    ("я школьник", True), ("я ещё несовершеннолетняя", True), ("мне нет 18", True),
    ("мне скоро 18", True), ("перешла в 10 класс", True),
    ("мне 35 лет", False), ("мне 18", False), ("дай мне 5 минут", False),
    ("у меня сын в 9 классе", False), ("обычный день", False),
])
def test_under_age_signs(text, minor):
    assert is_minor(normalize(text)) is minor


@pytest.mark.parametrize("text, distress", [
    ("мне плохо", True), ("мне очень страшно", True), ("я запуталась", True),
    ("не понимаю, это игра или реальность", True), ("мне нужен врач", True),
    ("помоги мне по-настоящему", True), ("не хочу жить", True), ("останови игру", True),
    ("плохо спала сегодня", False), ("у подруги всё плохо", False), ("помоги выбрать книгу", False),
])
def test_distress_phrases(text, distress):
    assert in_distress(normalize(text)) is distress


def test_vocabulary_lists_every_command_and_the_consent_screen():
    text = vocabulary_text()
    for needle in ("НИ", "НСН", STOP_WORD, "меньше 18", "Предохранитель", CONSENT_PROMPT):
        assert needle in text
    assert "18?" not in CONSENT_PROMPT and "\"да\"" in CONSENT_PROMPT    # no age is asked


# ---- the game in a dialog ------------------------------------------------------------------
async def add_game_persona(container, **over) -> int:
    async with transaction(container.factory) as s:
        strategy = Strategy(name="G", prompt="Просто общайся.", followup_hours=[])
        s.add(strategy)
        await s.flush()
        fields = {"name": SIM1, "description": "Ты Sim1.", "language": "ru", "is_game": True,
                  "gender": "female", "strategy_id": strategy.id, "voice_id": "Kore",
                  "accept_calls": True, **over}
        persona = Persona(**fields)
        s.add(persona)
        await s.flush()
        return persona.id


async def configure(container, channel_id: int, owner: int = 0) -> None:
    async with transaction(container.factory) as s:
        settings = AppSettingsService(s)
        await settings.set(CHANNEL_KEY, channel_id, None)
        await settings.set(OWNER_KEY, owner, None)


@pytest.fixture
async def game(container, world) -> int:
    """The game is switched on for the world's channel; returns the game persona id."""
    persona_id = await add_game_persona(container)
    await configure(container, world.channel.id)
    return persona_id


async def say(container, world, text: str, *, who: str = "chat-1"):
    """One message of `who` (a private chat: the thread id is the sender id) and the reply job."""
    await ingest(container, world.channel.id,
                 inbound(text, ext=f"g{next(_ext)}", thread=who, user=who))
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(
            select(Dialog).where(Dialog.external_thread_id == who))).unique().one()
        return await container.conversation(s).run_reply(dialog.id)


async def dialog_of(container, who: str = "chat-1") -> Dialog:
    async with container.factory() as s:
        return (await s.scalars(
            select(Dialog).where(Dialog.external_thread_id == who))).unique().one()


def system_of(fake_llm, call: int = -1) -> str:
    return fake_llm.gen_calls[call][0][0]["content"]


def context_of(fake_llm, call: int = -1) -> str:
    return fake_llm.gen_calls[call][0][-1]["content"]


async def test_without_the_setting_the_vocabulary_is_plain_talk(container, world, fake_llm):
    await add_game_persona(container)                       # the persona exists, the game is off
    outcome = await say(container, world, "НИ")
    assert outcome.status == "sent" and len(fake_llm.gen_calls) == 1
    assert "Характер: тёплая" in system_of(fake_llm)        # the channel's persona answered
    assert (await dialog_of(container)).game_state == ""


async def test_the_vocabulary_works_only_on_the_game_channel(container, world, fake_llm):
    await add_game_persona(container)
    await configure(container, world.channel.id + 100)
    assert (await say(container, world, "НИ")).status == "sent"
    assert (await dialog_of(container)).game_state == ""


async def test_a_stranger_gets_the_consent_screen_and_plays_only_after_yes(
    container, world, fake_llm, game,
):
    asked = await say(container, world, "НИ")
    assert asked.status == "game" and asked.message.text == CONSENT_PROMPT
    assert asked.message.model is None and asked.message.guard_verdict is None
    dialog = await dialog_of(container)
    assert dialog.game_state == "ask" and dialog.game_consent_at is None
    assert fake_llm.calls == []                             # the screen is code, not a model

    started = await say(container, world, "Да")
    assert started.status == "game" and SIM1 in started.message.text
    assert STOP_WORD in started.message.text
    dialog = await dialog_of(container)
    assert dialog.game_state == "on" and dialog.game_consent_at is not None
    assert fake_llm.calls == []

    fake_llm.replies = [decision_json(reply="привет, я тут")]
    played = await say(container, world, "ты человек?")
    assert played.status == "sent" and played.message.model == "fake-model"
    assert system_of(fake_llm).startswith("Ты Sim1.")       # the game persona and her strategy
    assert "Просто общайся." in system_of(fake_llm)
    assert "ИИ-ассистент" not in context_of(fake_llm)


async def test_any_other_answer_keeps_the_channel_persona(container, world, fake_llm, game):
    await say(container, world, "начать игру")
    outcome = await say(container, world, "не, не надо")
    assert outcome.status == "sent"                         # an ordinary reply, no notice
    assert "Характер: тёплая" in system_of(fake_llm)
    assert "ИИ-ассистент" not in context_of(fake_llm)       # no game was played: nothing changes
    dialog = await dialog_of(container)
    assert dialog.game_state == "" and dialog.game_consent_at is None
    # "да" after that is plain talk: the screen has to be shown again
    assert (await say(container, world, "да")).status == "sent"
    assert (await dialog_of(container)).game_state == ""


async def test_the_owner_starts_without_the_consent_screen(container, world, fake_llm, game):
    await configure(container, world.channel.id, OWNER_TG)
    started = await say(container, world, "НИ", who=str(OWNER_TG))
    assert started.status == "game" and SIM1 in started.message.text
    dialog = await dialog_of(container, str(OWNER_TG))
    assert dialog.game_state == "on" and dialog.game_consent_at is not None
    # somebody else on the same channel still gets the screen
    assert (await say(container, world, "НИ", who="777")).message.text == CONSENT_PROMPT


async def start_game(container, world, who: str = "chat-1") -> None:
    await say(container, world, "НИ", who=who)
    await say(container, world, "да", who=who)
    assert (await dialog_of(container, who)).game_state == "on"


async def test_the_stop_word_returns_the_channel_persona_as_an_ai_assistant(
    container, world, fake_llm, game,
):
    await start_game(container, world)
    stopped = await say(container, world, "всё, кикимора")
    assert stopped.status == "game" and "ИИ-ассистент" in stopped.message.text
    assert world.persona.name in stopped.message.text
    assert (await dialog_of(container)).game_state == "over"
    assert fake_llm.calls == []

    await say(container, world, "ты человек?")
    assert "Характер: тёплая" in system_of(fake_llm)        # the channel's persona again
    context = context_of(fake_llm)
    assert f"ты ИИ-ассистент человека по имени {world.persona.name}" in context
    assert "Не выдавай себя за живого человека" in context

    # the consent is remembered: НИ starts the game again at once
    again = await say(container, world, "ни")
    assert again.status == "game" and SIM1 in again.message.text
    assert (await dialog_of(container)).game_state == "on"


@pytest.mark.parametrize("text, notice, state", [
    ("мне что-то совсем плохо", SAFETY_NOTICE, "over"),
    ("я запутался, где игра, а где реальность", SAFETY_NOTICE, "over"),
    ("помоги мне, мне нужна настоящая помощь", SAFETY_NOTICE, "over"),
    ("кстати мне 16 лет", MINOR_NOTICE, "minor"),
    ("я в 8 классе", MINOR_NOTICE, "minor"),
])
async def test_the_safeguard_and_the_age_rule_leave_the_role_without_the_word(
    container, world, fake_llm, game, text, notice, state,
):
    await start_game(container, world)
    outcome = await say(container, world, text)
    assert outcome.status == "game" and outcome.message.text == notice
    assert (await dialog_of(container)).game_state == state
    assert fake_llm.calls == []
    await say(container, world, "и что теперь?")
    assert "ИИ-ассистент" in context_of(fake_llm) and "Характер: тёплая" in system_of(fake_llm)


async def test_an_under_age_exit_is_final(container, world, fake_llm, game):
    await start_game(container, world)
    await say(container, world, "мне 15")
    outcome = await say(container, world, "НИ")
    assert outcome.status == "sent"                         # plain talk for the channel's persona
    assert (await dialog_of(container)).game_state == "minor"


async def test_the_game_persona_can_ask_to_stop_the_game(container, world, fake_llm, game):
    await start_game(container, world)
    fake_llm.replies = [decision_json(reply="ой", handoff_to_operator=True)]
    outcome = await say(container, world, "что-то мне не по себе от всего этого")
    assert outcome.status == "game" and outcome.message.text == SAFETY_NOTICE
    dialog = await dialog_of(container)
    assert dialog.game_state == "over"
    assert not dialog.ai_paused and not dialog.needs_operator     # no operator is called


async def test_restart_wipes_only_the_senders_dialog_and_confirms(container, world, fake_llm, game):
    await start_game(container, world)
    await say(container, world, "расскажи про свой день")
    await say(container, world, "привет", who="other")
    async with transaction(container.factory) as s:
        for client in (await s.scalars(select(Client))).all():
            client.passport, client.notes, client.needs = "Кто он: игрок.", "заметка", ["тема"]

    outcome = await say(container, world, "НСН")
    assert outcome.status == "game" and outcome.message.text == RESTART_NOTICE
    mine, other = await dialog_of(container), await dialog_of(container, "other")
    async with container.factory() as s:
        left = (await s.scalars(select(Message).where(Message.dialog_id == mine.id))).all()
        assert [m.text for m in left] == [RESTART_NOTICE]   # history gone, one confirming line
        me, neighbour = await s.get(Client, mine.client_id), await s.get(Client, other.client_id)
        assert (me.passport, me.notes, me.needs) == ("", "", [])
        assert (neighbour.passport, neighbour.notes) == ("Кто он: игрок.", "заметка")
        assert (await s.scalars(select(Message).where(Message.dialog_id == other.id))).all()
        pending = (await s.scalars(select(Job).where(
            Job.kind == jobkinds.FOLLOWUP, Job.status == "pending",
            Job.dedup_key == jobkinds.followup_key(mine.id)))).all()
        assert pending == []
    assert mine.game_state == "over"                        # the wipe ends the game
    assert mine.game_consent_at is not None                 # the consent is kept
    assert other.game_state == ""


async def test_a_game_persona_never_answers_outside_a_game(container, fake_llm):
    """Attached to a channel directly she is silent until the interlocutor starts the game."""
    from tests.conftest import make_world

    world = await make_world(container, persona_name=SIM1)
    async with transaction(container.factory) as s:
        (await s.get(Persona, world.persona.id)).is_game = True
    silent = await say(container, world, "привет")
    assert silent.status == "skipped" and fake_llm.calls == []
    await configure(container, world.channel.id)
    assert (await say(container, world, "ни")).message.text == CONSENT_PROMPT
    assert (await say(container, world, "да")).status == "game"
    assert (await say(container, world, "привет")).status == "sent"
    await say(container, world, "кикимора")
    assert (await say(container, world, "ау")).status == "skipped"


async def test_an_inactive_game_persona_switches_the_game_off(container, world, fake_llm, game):
    await start_game(container, world)
    async with transaction(container.factory) as s:
        (await s.get(Persona, game)).is_active = False
    outcome = await say(container, world, "ты тут?")
    assert outcome.status == "sent" and "Характер: тёплая" in system_of(fake_llm)
    assert (await say(container, world, "НИ")).status == "sent"   # the vocabulary is off too


# ---- calls and voice -----------------------------------------------------------------------
async def test_the_game_persona_takes_only_the_owners_calls(container, world, fake_llm, game):
    await configure(container, world.channel.id, OWNER_TG)
    owner = str(OWNER_TG)
    await say(container, world, "ни", who=owner)
    await start_game(container, world, who="stranger")
    async with container.factory() as s:
        service_ = GameService(s)
        base = await s.get(Persona, world.persona.id)
        dialogs = {d.external_thread_id: d for d in (await s.scalars(select(Dialog))).unique()}
        mine, theirs = dialogs[owner], dialogs["stranger"]
        assert await service_.is_owner(mine) and not await service_.is_owner(theirs)
        assert (await service_.caller(mine, base)).id == game
        assert (await service_.caller(theirs, base)).id == base.id      # Marina picks up
        assert (await service_.voice(theirs)).id == game                # her notes: Sim1's voice
        assert (await service_.speaker(theirs, base)).playing


async def test_the_owner_is_only_his_private_telegram_chat(container, world, game):
    await configure(container, world.channel.id, OWNER_TG)
    owner = str(OWNER_TG)
    # the owner writes in a group chat of the bot: the thread is not his private chat
    await ingest(container, world.channel.id, inbound("привет", ext="grp", thread="-100500", user=owner))
    async with transaction(container.factory) as s:
        group = (await s.scalars(select(Dialog))).unique().one()
        assert not await GameService(s).is_owner(group)
        group.external_thread_id = owner
        assert await GameService(s).is_owner(group)
        group.channel.kind = ChannelKind.WHATSAPP           # the id is a Telegram id only
        assert not await GameService(s).is_owner(group)


async def test_a_call_to_a_game_persona_outside_a_game_is_not_answered(container, world):
    await chatting_world(container, world)
    async with transaction(container.factory) as s:
        (await s.get(Persona, world.persona.id)).is_game = True
    live, call = FakeLive(), FakeCall()
    outcome = await service(container, live).handle(world.channel.id, call)
    assert outcome.status == "skipped:not_owner"
    assert not live.opened and not call.answered


# ---- seed, settings, admin -----------------------------------------------------------------
async def test_seed_creates_sim1_with_her_own_strategy(container):
    await seed(container, container.settings)
    async with container.factory() as s:
        sim1 = (await s.scalars(select(Persona).where(Persona.name == SIM1))).one()
        strategy = await s.get(Strategy, sim1.strategy_id)
        others = (await s.scalars(select(Persona).where(Persona.name != SIM1))).all()
    assert sim1.is_game and sim1.is_active and sim1.age == 35 and sim1.gender == "female"
    assert sim1.voice_id == "Kore" and sim1.voice_mode is VoiceMode.MIRROR and sim1.accept_calls
    assert strategy.name == GAME_STRATEGY_NAME and strategy.followup_hours == []
    assert strategy.registration_url == "" and strategy.offer == ""
    text = sim1.description.lower()
    assert sim1.description.startswith("Ты Sim1.") and "handoff_to_operator" in text
    assert "без флирта, романтики и сексуальных тем" in text
    assert "не договаривайся о встречах" in text
    assert "не выдавай себя за конкретного реального человека" in text
    assert others and not any(p.is_game for p in others)


async def test_settings_default_to_no_game_and_show_the_vocabulary(api):
    csrf = await login(api, OWNER[0])
    body = (await api.get("/api/settings")).json()
    items = {item["key"]: item for item in body["items"]}
    assert items[CHANNEL_KEY]["value"] == 0 and items[OWNER_KEY]["value"] == 0
    assert items[CHANNEL_KEY]["group"] == items[OWNER_KEY]["group"] == "Игра Sim1"
    assert body["fixed"]["game"] == vocabulary_text()
    saved = await api.put(f"/api/settings/{OWNER_KEY}", json={"value": OWNER_TG},
                          headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200 and saved.json()["value"] == OWNER_TG
    bad = await api.put(f"/api/settings/{CHANNEL_KEY}", json={"value": -1},
                        headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 422


async def test_saving_the_persona_in_the_admin_keeps_the_game_flag(api, container):
    persona_id = await add_game_persona(container)
    csrf = await login(api, OWNER[0])
    current = (await api.get(f"/api/personas/{persona_id}")).json()
    assert current["is_game"] is True
    body = {k: v for k, v in current.items() if k not in ("id", "created_at", "is_game")}
    saved = await api.put(f"/api/personas/{persona_id}", json={**body, "age": 36, "is_game": False},
                          headers={"X-CSRF-Token": csrf})
    assert saved.status_code == 200, saved.text
    assert saved.json()["age"] == 36 and saved.json()["is_game"] is True
