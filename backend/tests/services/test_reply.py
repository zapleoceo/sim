from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.clock import FixedClock
from sim.db.models import (
    Chat,
    ChatEvent,
    Client,
    Generation,
    LlmCall,
    MediaAsset,
    Message,
    Persona,
    ReplyRun,
)
from sim.db.session import transaction
from sim.domain.enums import (
    Author,
    Direction,
    EventKind,
    LlmPurpose,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    RunStatus,
    UnderstandingStatus,
)
from sim.domain.plan import ReplyPlan
from sim.events import append_event
from sim.ports import PortError
from sim.services.deps import Deps
from sim.services.reply import ReplyService
from tests.services.conftest import Fakes
from tests.services.factories import (
    add_message,
    all_rows,
    block_verdict,
    fresh,
    make_chat,
    make_media,
    make_persona,
    ok_verdict,
    plan_json,
)

TEXT = {"type": "text", "text": "привет!"}


async def setup_chat(db: AsyncSession, clock: FixedClock, *, ago_s: float = 60,
                     **persona: Any) -> Chat:
    chat = await make_chat(db, await make_persona(db, **persona))
    await add_message(db, chat, "привет, как дела?", at=clock.now() - timedelta(seconds=ago_s))
    return chat


async def outgoing(db: AsyncSession, chat: Chat) -> list[Message]:
    return await all_rows(db, select(Message).where(Message.chat_id == chat.id,
                                                    Message.direction == Direction.OUT)
                          .order_by(Message.id))


async def events_of(db: AsyncSession, chat: Chat) -> list[EventKind]:
    rows = await all_rows(db, select(ChatEvent).where(ChatEvent.chat_id == chat.id)
                          .order_by(ChatEvent.id))
    return [row.kind for row in rows]


async def runs(db: AsyncSession, chat: Chat) -> list[ReplyRun]:
    return await all_rows(db, select(ReplyRun).where(ReplyRun.chat_id == chat.id))


async def test_text_reply_passes_the_guard_and_is_queued(db: AsyncSession, deps: Deps,
                                                         fakes: Fakes, clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", plan_json(TEXT)).script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    run = (await runs(db, chat))[0]
    assert run.status == RunStatus.SENT and run.attempts == 1
    assert run.guard_verdicts == [{"ok": True, "reason": "", "attempt": 1}]
    assert run.plan["messages"][0]["text"] == "привет!"
    assert run.context["timeline"][-1].endswith("Дима: привет, как дела?")
    sent = await outgoing(db, chat)
    assert [(m.text, m.kind, m.status, m.author) for m in sent] == [
        ("привет!", MessageKind.TEXT, MessageStatus.PENDING, Author.PERSONA)]
    assert fakes.tasks.of("send") == [("send", sent[0].id, chat.id)]
    calls = await all_rows(db, select(LlmCall).where(LlmCall.reply_run_id == run.id)
                           .order_by(LlmCall.id))
    assert [c.purpose for c in calls] == [LlmPurpose.REPLY, LlmPurpose.GUARD]
    first = fakes.llm.calls_for("reply-model")[0]
    assert "Ты Марина" in (first["messages"][0].parts[0].text or "")
    assert first["json_schema"] == ReplyPlan.model_json_schema()
    assert "Твой ход" in (first["messages"][1].parts[0].text or "")


async def test_guard_block_then_retry_ok(db: AsyncSession, deps: Deps, fakes: Fakes,
                                         clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", plan_json({"type": "text", "text": "я бот"}),
                     plan_json({"type": "text", "text": "я Марина"}))
    fakes.llm.script("guard-model", block_verdict("признаётся, что бот"), ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SENT
    assert [m.text for m in await outgoing(db, chat)] == ["я Марина"]
    second = fakes.llm.calls_for("reply-model")[1]["messages"]
    assert "признаётся, что бот" in (second[-1].parts[0].text or "")
    assert second[-2].role == "assistant"
    run = (await runs(db, chat))[0]
    assert run.attempts == 2 and [v["ok"] for v in run.guard_verdicts] == [False, True]
    assert (await events_of(db, chat)).count(EventKind.GUARD_BLOCKED) == 1
    assert not (await fresh(db, chat)).needs_operator


async def test_second_block_needs_an_operator(db: AsyncSession, deps: Deps, fakes: Fakes,
                                              clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.always("reply-model", plan_json(TEXT))
    fakes.llm.script("guard-model", block_verdict("не тот язык"), block_verdict("опять"))

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.BLOCKED
    assert await outgoing(db, chat) == []
    assert fakes.tasks.of("send") == []
    assert (await fresh(db, chat)).needs_operator
    kinds = await events_of(db, chat)
    assert kinds.count(EventKind.GUARD_BLOCKED) == 2 and EventKind.OPERATOR_NEEDED in kinds
    run = (await runs(db, chat))[0]
    assert run.status == RunStatus.BLOCKED and "опять" in (run.error or "")
    # While the chat waits for an operator the persona stays silent.
    assert (await ReplyService(deps).run(chat.id)).reason == "waiting for an operator"


async def test_invalid_json_is_repaired_once(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", "привет, я не JSON", plan_json(TEXT))
    fakes.llm.always("guard-model", ok_verdict())

    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT
    repair = fakes.llm.calls_for("reply-model")[1]["messages"]
    assert "Ответ не подошёл" in (repair[-1].parts[0].text or "")


async def test_plan_still_invalid_after_repair_fails_the_run(db: AsyncSession, deps: Deps,
                                                             fakes: Fakes,
                                                             clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", "не JSON", plan_json({"type": "photo", "media_id": 999}))

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.FAILED
    run = (await runs(db, chat))[0]
    assert run.status == RunStatus.FAILED and "media_id 999" in (run.error or "")
    assert await outgoing(db, chat) == []


@pytest.mark.parametrize("switch", ["persona", "client", "chat"])
async def test_ai_off_anywhere_skips_without_a_run(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                   clock: FixedClock, switch: str) -> None:
    chat = await setup_chat(db, clock)
    if switch == "persona":
        persona = await db.get(Persona, chat.persona_id)
        assert persona is not None
        persona.ai_enabled = False
    elif switch == "client":
        client = await db.get(Client, chat.client_id)
        assert client is not None
        client.ai_enabled = False
    else:
        chat.ai_enabled = False
    await db.flush()

    result = await ReplyService(deps).run(chat.id)

    assert result.status is None and result.reason == "AI is off"
    assert await runs(db, chat) == []
    assert fakes.llm.calls == []


async def test_nothing_to_answer_after_an_outgoing_message(db: AsyncSession, deps: Deps,
                                                           clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    await add_message(db, chat, "я тут", at=clock.now(), direction=Direction.OUT,
                      author=Author.OPERATOR)
    result = await ReplyService(deps).run(chat.id)
    assert result.status is None and result.reason == "nothing to answer"


async def test_waits_for_a_burst_of_messages(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    chat = await setup_chat(db, clock, ago_s=2)
    result = await ReplyService(deps).run(chat.id)
    assert result.status is None and result.requeue_s == pytest.approx(6.0)
    assert fakes.tasks.of("reply") == [("reply", chat.id, pytest.approx(6.0))]
    assert await runs(db, chat) == []


async def test_waits_for_understanding_then_answers_anyway(db: AsyncSession, deps: Deps,
                                                           fakes: Fakes,
                                                           clock: FixedClock) -> None:
    chat = await make_chat(db, await make_persona(db))
    photo = await make_media(db, None, source=MediaSource.INCOMING,
                             understanding=UnderstandingStatus.PENDING)
    await add_message(db, chat, "", kind=MessageKind.PHOTO, media=photo,
                      at=clock.now() - timedelta(seconds=30))

    first = await ReplyService(deps).run(chat.id)
    assert first.reason == "attachments are being understood" and first.requeue_s == 5.0

    clock.advance(seconds=120)
    fakes.llm.script("reply-model", plan_json(TEXT)).script("guard-model", ok_verdict())
    second = await ReplyService(deps).run(chat.id)
    assert second.status == RunStatus.SENT
    user = fakes.llm.calls_for("reply-model")[0]["messages"][1].parts[0].text or ""
    assert "[фото, ещё не успела посмотреть]" in user


async def test_newer_client_message_discards_the_stale_answer(
    db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    chat = await setup_chat(db, clock)

    async def answer_while_client_writes(record: dict[str, Any]) -> str:
        async with transaction(sessions) as session:
            message = Message(chat_id=chat.id, direction=Direction.IN, author=Author.CLIENT,
                              kind=MessageKind.TEXT, text="ты тут?",
                              status=MessageStatus.RECEIVED, external_id="late-1",
                              sent_at=clock.now())
            session.add(message)
            await session.flush()
            await append_event(session, chat_id=chat.id, kind=EventKind.MESSAGE_IN,
                               actor="client", message_id=message.id)
        return plan_json(TEXT)

    fakes.llm.script("reply-model", answer_while_client_writes)
    fakes.llm.script("guard-model", ok_verdict())

    result = await ReplyService(deps).run(chat.id)

    assert result.status == RunStatus.SKIPPED
    assert await outgoing(db, chat) == []
    assert fakes.tasks.of("reply") == [("reply", chat.id, 8.0)]
    assert (await runs(db, chat))[0].status == RunStatus.SKIPPED


async def test_remember_adds_facts_without_duplicates(db: AsyncSession, deps: Deps,
                                                      fakes: Fakes, clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    client = await db.get(Client, chat.client_id)
    assert client is not None
    client.memory = [{"fact": "Живёт в Киеве", "event_id": None, "at": "2026-11-01T00:00:00"}]
    await db.flush()
    fakes.llm.script("reply-model",
                     plan_json(TEXT, remember=["живёт в Киеве.", "Работает врачом"]))
    fakes.llm.script("guard-model", ok_verdict())

    await ReplyService(deps).run(chat.id)

    facts = [item["fact"] for item in (await fresh(db, client)).memory]
    assert facts == ["Живёт в Киеве", "Работает врачом"]


async def test_empty_plan_is_silence_without_a_guard_call(db: AsyncSession, deps: Deps,
                                                          fakes: Fakes,
                                                          clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", plan_json())
    result = await ReplyService(deps).run(chat.id)
    assert result.status == RunStatus.SENT
    assert fakes.llm.calls_for("guard-model") == []
    assert await outgoing(db, chat) == []


async def test_retryable_broker_error_fails_the_run_and_raises(db: AsyncSession, deps: Deps,
                                                               fakes: Fakes,
                                                               clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", PortError("timeout", retryable=True))
    with pytest.raises(PortError):
        await ReplyService(deps).run(chat.id)
    run = (await runs(db, chat))[0]
    assert run.status == RunStatus.FAILED and run.error == "timeout"
    errors = await all_rows(db, select(LlmCall).where(LlmCall.reply_run_id == run.id))
    assert [e.error for e in errors] == ["timeout"]


async def test_full_plan_photo_voice_and_generation(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                    clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    persona_id = chat.persona_id
    photo = await make_media(db, persona_id, caption="селфи у окна", tags=["селфи"])
    await make_media(db, persona_id, is_reference=True, storage=fakes.storage)
    fakes.llm.script("reply-model", plan_json(
        TEXT,
        {"type": "photo", "media_id": photo.id, "caption": "это я"},
        {"type": "voice", "text": "приветик"},
        {"type": "video", "caption": "сейчас сниму",
         "generate": {"prompt": "танцую дома", "caption": "танец дома", "tags": ["танец"]}},
    ))
    fakes.llm.script("guard-model", ok_verdict())

    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT

    sent = await outgoing(db, chat)
    assert [(m.kind, m.text) for m in sent] == [
        (MessageKind.TEXT, "привет!"), (MessageKind.PHOTO, "это я"), (MessageKind.VOICE, ""),
        (MessageKind.TEXT, "сейчас сниму")]
    assert sent[1].media_id == photo.id
    voice = await db.get(MediaAsset, sent[2].media_id)
    assert voice is not None and voice.kind == MediaKind.AUDIO
    assert voice.source == MediaSource.GENERATED and voice.description == "приветик"
    assert [c[1] for c in fakes.tasks.of("send")] == [m.id for m in sent]
    generations = await all_rows(db, select(Generation).where(Generation.chat_id == chat.id))
    assert len(generations) == 1 and generations[0].caption == "танец дома"
    assert fakes.tasks.of("generate") == [("generate", generations[0].id)]
    assert fakes.speech.calls[0]["voice"] == "Vindemiatrix"


# --- read receipts and live statuses ---------------------------------------------------------


async def test_turn_reads_the_chat_then_types(db: AsyncSession, deps: Deps, fakes: Fakes,
                                              clock: FixedClock) -> None:
    chat = await make_chat(db, await make_persona(db))
    await add_message(db, chat, "ты тут?", at=clock.now() - timedelta(seconds=90))
    last, _ = await add_message(db, chat, "ау", at=clock.now() - timedelta(seconds=60))
    fakes.llm.script("reply-model", plan_json(TEXT)).script("guard-model", ok_verdict())

    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT

    kinds = [call[0] for call in fakes.tasks.calls]
    assert kinds[:2] == ["mark_read", "chat_action"]
    assert fakes.tasks.of("mark_read") == [("mark_read", last.id)]
    assert fakes.tasks.of("chat_action") == [("chat_action", chat.id, "typing", 40.0)]
    assert kinds[-1] == "send"                      # the send ends «печатает»; no cancel


async def test_silence_cancels_the_status(db: AsyncSession, deps: Deps, fakes: Fakes,
                                          clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", plan_json())
    await ReplyService(deps).run(chat.id)
    assert fakes.tasks.actions() == ["typing", "cancel"]


async def test_a_blocked_turn_cancels_the_status(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                 clock: FixedClock) -> None:
    blocked = await setup_chat(db, clock)
    fakes.llm.always("reply-model", plan_json(TEXT))
    fakes.llm.script("guard-model", block_verdict("нет"), block_verdict("нет"))
    assert (await ReplyService(deps).run(blocked.id)).status == RunStatus.BLOCKED
    assert fakes.tasks.actions() == ["typing", "cancel"]


async def test_a_failed_turn_cancels_the_status(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                clock: FixedClock) -> None:
    failing = await setup_chat(db, clock)
    fakes.llm.script("reply-model", PortError("timeout", retryable=True))
    with pytest.raises(PortError):
        await ReplyService(deps).run(failing.id)
    assert fakes.tasks.actions() == ["typing", "cancel"]


async def test_ai_off_is_neither_read_nor_typed(db: AsyncSession, deps: Deps, fakes: Fakes,
                                                clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    chat.ai_enabled = False
    await db.flush()
    await ReplyService(deps).run(chat.id)
    assert fakes.tasks.calls == []


async def test_each_slow_action_shows_its_status_before_the_work(
    db: AsyncSession, deps: Deps, fakes: Fakes, clock: FixedClock,
) -> None:
    chat = await setup_chat(db, clock)
    fakes.llm.script("reply-model", plan_json(
        {"type": "voice", "text": "приветик"},
        {"type": "video_note", "generate": {"prompt": "машу рукой на кухне",
                                            "caption": "привет с кухни", "tags": [],
                                            "speech": "Дима, привет! Вот я на кухне."}},
    ))
    fakes.llm.script("guard-model", ok_verdict())
    seen_by_tts: list[list[str]] = []
    synthesize = fakes.speech.synthesize

    async def tts_after_status(**kwargs: Any) -> Any:
        seen_by_tts.append(fakes.tasks.actions())
        return await synthesize(**kwargs)

    fakes.speech.synthesize = tts_after_status  # type: ignore[method-assign]

    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT

    assert fakes.tasks.actions() == ["typing", "record_audio", "record_round"]
    statuses = fakes.tasks.of("chat_action")
    assert statuses[1][3] == 30.0 and statuses[2][3] == 400.0
    # «записывает голосовое» is asked for before the TTS call is made.
    assert seen_by_tts == [["typing", "record_audio"]]
    assert len(fakes.tasks.of("send")) == 1
    [generation] = await all_rows(db, select(Generation).where(Generation.chat_id == chat.id))
    assert generation.speech == "Дима, привет! Вот я на кухне."
    assert fakes.tasks.of("generate") == [("generate", generation.id)]


async def test_a_video_note_without_speech_is_repaired(db: AsyncSession, deps: Deps,
                                                       fakes: Fakes, clock: FixedClock) -> None:
    chat = await setup_chat(db, clock)
    spec = {"prompt": "машу рукой", "caption": "привет", "tags": []}
    fakes.llm.script("reply-model",
                     plan_json({"type": "video_note", "generate": spec}),
                     plan_json({"type": "video_note",
                                "generate": {**spec, "speech": "Привет, Дима!"}}))
    fakes.llm.script("guard-model", ok_verdict())

    assert (await ReplyService(deps).run(chat.id)).status == RunStatus.SENT
    repair = fakes.llm.calls_for("reply-model")[1]["messages"][-1].parts[0].text or ""
    assert "generate.speech" in repair
