"""The journal of the persona's media decisions: what is written into the dialog when she sends,
declines or postpones a photo or a video note, how the record follows the generation, and how
her earlier decisions come back in the context of the next replies. No real Broker or channel."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from sim.adapters.db.models import Channel, Client, Dialog, Job, Message, Persona
from sim.adapters.db.session import transaction
from sim.api.schemas import message_out
from sim.domain.enums import Author, ChannelKind, Delivery, Direction, MediaKind, SessionStatus
from sim.ports.image import ImageError
from sim.services import jobkinds
from sim.services.conversation import decision_fields, history_turns
from sim.services.media_decisions import (
    BROKER_REFUSED,
    DECLINED,
    EARLIER,
    ENDED,
    FAILED,
    GENERATED,
    MARKER,
    POSTPONED,
    QUEUED,
    SENT,
    UNAVAILABLE,
    MediaDecision,
    asked_kind,
    load,
    reason_warning,
)
from sim.services.persona_media_tool import MEDIA_REPORT, NO_MEDIA
from sim.services.prompt import Decision, MediaAsk, parse_media
from sim.worker.handlers import reply_detail
from tests.conftest import decision_json
from tests.test_conversation import run_reply
from tests.test_persona_media_tool import (
    ASK,
    MODEL,
    SCENE,
    FakeImages,
    PhotoPort,
    add_photo,
    context_of,
    media_jobs,
    rows,
    run_media_job,
    say,
    set_setting,
)

OWNER_TG = 777000111
ROUTINE = "пн-пт 06:30-07:15 пробежка вдоль реки\nежедневно 23:00-06:30 сплю"
MONDAY_RUN = datetime(2026, 11, 2, 5, 0)        # 07:00 in Kyiv: the morning run
MONDAY_NOON = datetime(2026, 11, 2, 10, 0)      # 12:00 in Kyiv: nothing in the routine
DECLINE = {"kind": "photo", "decision": "decline", "reason": "не хочу сейчас фоткаться"}
STAMP = r"- \d\d\.\d\d \d\d:\d\d "


@pytest.fixture
async def photos(container, world, connector) -> tuple[PhotoPort, FakeImages]:
    """A dialog where a photo is on offer; the client has not written yet."""
    connector.port = PhotoPort()
    container.image = FakeImages()
    await set_setting(container, "persona_photo_model", MODEL)
    await add_photo(container, world.persona.id)
    return connector.port, container.image


async def records(container) -> list[tuple[Message, MediaDecision]]:
    """The service records of all dialogs with their decisions, oldest first."""
    return [(m, load(m)) for m in await rows(container, Message) if m.media_kind == MARKER]


async def reply_with(container, fake_llm, **decision):
    fake_llm.replies = [decision_json(**decision)]
    return await run_reply(container)


async def owner_dialog(container, world, text: str) -> int:
    """The private dialog of a Telegram userbot channel with the allowlisted owner account, who
    has just written `text`."""
    await set_setting(container, "video_note_owner_telegram_id", OWNER_TG)
    async with transaction(container.factory) as s:
        channel = Channel(kind=ChannelKind.TELEGRAM_USER, persona_id=world.persona.id, name="userbot",
                          status=SessionStatus.ACTIVE,
                          session_enc=container.box.encrypt_json({"session": "x"}))
        s.add(channel)
        await s.flush()
        client = Client(persona_id=world.persona.id, ref_code="own000001")
        s.add(client)
        await s.flush()
        dialog = Dialog(channel_id=channel.id, external_thread_id=str(OWNER_TG), client_id=client.id)
        s.add(dialog)
        await s.flush()
        s.add(Message(dialog_id=dialog.id, direction=Direction.IN, author=Author.CLIENT, text=text,
                      delivery=Delivery.SENT, external_id="own-1"))
        return dialog.id


# ---- how she reports a decision ----------------------------------------------------------------

def test_a_refusal_or_a_postponement_is_parsed_with_its_reason():
    declined = parse_media({"kind": "video_note", "decision": " Decline ", "reason": " гуляю\nна улице "})
    assert declined == MediaAsk(MediaKind.VIDEO_NOTE, decision="decline", reason="гуляю на улице")
    assert not declined.send and declined.scene == ""
    later = parse_media({"kind": "photo", "decision": "later", "scene": "не нужна"})
    assert (later.decision, later.reason, later.scene) == ("later", "", "")
    # an unknown decision reads as a request to send, which needs a scene
    sent = parse_media({"kind": "photo", "decision": "yes", "scene": SCENE, "reason": "попросили"})
    assert sent == MediaAsk(MediaKind.PHOTO, SCENE, reason="попросили") and sent.send
    assert parse_media({"kind": "photo", "decision": "yes"}) is None
    assert parse_media({"decision": "decline", "reason": "x"}) is None
    assert len(parse_media({"kind": "photo", "decision": "decline", "reason": "я" * 900}).reason) == 300


def test_the_guard_sees_the_decision_and_its_reason():
    declined = Decision(reply="не сейчас", media=MediaAsk(MediaKind.PHOTO, decision="decline", reason="устала"))
    assert decision_fields(declined)["media"] == {"kind": "photo", "decision": "decline", "reason": "устала"}
    sent = Decision(reply="вот", media=MediaAsk(MediaKind.PHOTO, SCENE))
    assert decision_fields(sent)["media"] == {"kind": "photo", "scene": SCENE}


@pytest.mark.parametrize(("text", "kind"), [
    ("Пришли кружочек", MediaKind.VIDEO_NOTE),
    ("запиши мне видео, пожалуйста, очень хочу тебя услышать", MediaKind.VIDEO_NOTE),
    ("а фото?", MediaKind.PHOTO),
    ("Можешь скинуть селфи, как ты сейчас выглядишь на работе?", MediaKind.PHOTO),
    ("покажи фото и кружочек тоже", MediaKind.VIDEO_NOTE),
    ("Я вчера весь вечер разбирал старые фотографии из отпуска с семьёй", None),
    ("Покажи, как ты сейчас", None),
    ("Привет", None),
])
def test_a_client_message_asks_for_media_when_it_names_it_and_requests(text, kind):
    assert asked_kind(text) is kind


def test_a_reason_is_checked_against_her_routine_at_that_moment():
    kyiv = "Europe/Kyiv"
    assert reason_warning("я на пробежке, неудобно", ROUTINE, kyiv, MONDAY_RUN) == ""
    assert reason_warning("гуляю на улице, у меня консультации", ROUTINE, kyiv, MONDAY_RUN) == \
        "причина не подтверждается распорядком (сейчас по распорядку: пробежка вдоль реки)"
    assert reason_warning("я на пробежке", ROUTINE, kyiv, MONDAY_NOON) == \
        "причина не подтверждается распорядком (на это время в распорядке записи нет)"
    assert reason_warning("на тренировке", "", kyiv, MONDAY_RUN).endswith("в распорядке записи нет)")
    assert reason_warning("", ROUTINE, kyiv, MONDAY_RUN) == "причина не названа"


def test_a_record_reads_as_one_tagged_entry_with_what_was_asked_decided_and_came_of_it():
    record = MediaDecision(
        requested="video_note", kind="video_note", decision="decline", outcome=DECLINED,
        reason="гуляю на улице", owner=True, offered=("photo", "video_note"),
        warning="причина не подтверждается распорядком (на это время в распорядке записи нет)")
    assert record.text().splitlines() == [
        "Решение медиа: запрос владельца, отказ модели",
        "Просили: кружочек",
        "Решение: не отправлять кружочек",
        "Причина: «гуляю на улице»",
        "Было доступно: фото, кружочек",
        "Исход: отказ модели",
        "Предупреждение: причина не подтверждается распорядком (на это время в распорядке записи нет)",
    ]
    assert record.context_line(datetime(2026, 11, 15, 14, 5)) == \
        "- 15.11 14:05 владелец просил кружочек, ты отказала, причина: «гуляю на улице»."
    assert record.journal() == {
        "requested": "video_note", "kind": "video_note", "decision": "decline", "outcome": DECLINED,
        "summary": "запрос владельца, отказ модели", "owner_request": True, "warning": record.warning}
    own_idea = MediaDecision(requested="none", kind="photo", decision="send", outcome=QUEUED,
                             scene=SCENE, owner=True, offered=("photo",))
    assert own_idea.headline == "модель решила отправить" and not own_idea.owner_request
    assert own_idea.text().splitlines()[1:] == [
        "Просили: ничего (инициатива модели)", "Решение: отправить фото", f"Сцена: {SCENE}",
        "Было доступно: фото", "Исход: запущена генерация"]


# ---- the record in the dialog ------------------------------------------------------------------

async def test_a_refusal_is_written_into_the_dialog_and_is_never_sent(container, world, fake_llm, photos):
    await say(container, world, "Пришли фото")
    outcome = await reply_with(container, fake_llm, reply="давай не сегодня", media=DECLINE)
    assert MEDIA_REPORT in context_of(fake_llm) and EARLIER not in context_of(fake_llm)
    ((row, record),) = await records(container)
    assert outcome.status == "sent" and outcome.media == record
    assert (record.requested, record.kind, record.decision, record.outcome) == \
        ("photo", "photo", "decline", DECLINED)
    assert record.reason == DECLINE["reason"] and record.offered == ("photo",) and not record.owner
    assert record.reply == ""                      # the reason is named: her text is not repeated
    assert record.warning.startswith("причина не подтверждается распорядком")
    assert (row.direction, row.author, row.delivery) == (Direction.OUT, Author.AI, Delivery.CANCELLED)
    assert row.text.startswith("Решение медиа: отказ модели\nПросили: фото\n")
    assert "Причина: «не хочу сейчас фоткаться»" in row.text and "Исход: отказ модели" in row.text
    assert row.error == record.warning and row.id > outcome.message.id
    # nothing is generated or sent because of the record, and the dialog preview is her text
    assert await media_jobs(container) == []
    jobs = await rows(container, Job)
    assert not [j for j in jobs if j.kind == jobkinds.SEND and j.payload["message_id"] == row.id]
    dialog = (await rows(container, Dialog))[0]
    assert dialog.last_message_text == "давай не сегодня"
    # the admin gets the record with its fields; the system log gets a short summary
    view = message_out(row)["media_decision"]
    assert view["headline"] == "отказ модели" and view["lines"][0] == "Просили: фото"
    assert (view["decision"], view["outcome"], view["owner_request"]) == ("decline", DECLINED, False)
    assert view["warning"] == record.warning
    assert message_out(outcome.message)["media_decision"] is None
    detail = reply_detail(outcome)
    assert detail["media"] == {"requested": "photo", "kind": "photo", "decision": "decline",
                               "outcome": DECLINED, "summary": "отказ модели", "warning": record.warning}
    # the models never read the record as a turn of the conversation
    assert all("Решение медиа" not in turn.text for turn in history_turns(await rows(container, Message)))


async def test_the_next_reply_is_reminded_of_the_refusal_and_its_reason(container, world, fake_llm, photos):
    await say(container, world, "Пришли фото")
    await reply_with(container, fake_llm, reply="давай не сегодня", media=DECLINE)
    await say(container, world, "ну почему?")
    await run_reply(container)
    context = context_of(fake_llm)
    assert EARLIER in context and "не противоречь" in EARLIER and "новых не придумывай" in EARLIER
    assert re.search(STAMP + "ранее ты решила не присылать фото, причина: «не хочу сейчас фоткаться»\\.",
                     context)
    # the block follows the offer and the instruction on how to report a decision
    assert context.index("МЕДИА:") < context.index(MEDIA_REPORT) < context.index(EARLIER)
    for message in fake_llm.gen_calls[-1][0][:-1]:
        assert "Решение медиа" not in message["content"]


async def test_a_request_she_answered_without_the_media_field_is_recorded_with_her_words(
        container, world, fake_llm, photos):
    await say(container, world, "Скинь фото, пожалуйста")
    outcome = await reply_with(container, fake_llm, reply="ой, я сейчас гуляю, потом")
    ((row, record),) = await records(container)
    assert outcome.decision.media is None
    assert (record.requested, record.decision, record.outcome) == ("photo", "decline", DECLINED)
    assert (record.reason, record.reply, record.warning) == ("", "ой, я сейчас гуляю, потом", "причина не названа")
    assert "Причина: не названа. Ответ модели: «ой, я сейчас гуляю, потом»" in row.text
    await say(container, world, "жаль")
    await run_reply(container)
    assert re.search(
        STAMP + "ранее ты решила не присылать фото, причину ты не записала, а собеседнику ответила: "
        "«ой, я сейчас гуляю, потом»\\.", context_of(fake_llm))


async def test_a_postponement_is_recorded_and_checked_against_the_routine(container, world, fake_llm, photos):
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.routine, persona.timezone = ROUTINE, "Europe/Kyiv"
    container.clock = lambda: MONDAY_RUN
    await say(container, world, "пришли фото")
    await reply_with(container, fake_llm, reply="после пробежки",
                     media={"kind": "photo", "decision": "later", "reason": "я на пробежке"})
    ((row, record),) = await records(container)
    assert (record.decision, record.outcome, record.warning) == ("later", POSTPONED, "")
    assert row.error is None and "Предупреждение" not in row.text
    assert row.text.startswith("Решение медиа: модель отложила\n") and "Исход: отложено моделью" in row.text
    await say(container, world, "жду")
    await run_reply(container)
    assert "- 02.11 07:00 ранее ты решила прислать позже фото, причина: «я на пробежке»." in context_of(fake_llm)


async def test_nothing_is_recorded_when_nobody_asked_for_media(container, world, fake_llm, photos):
    await say(container, world, "Здравствуйте, расскажите о курсе")
    outcome = await reply_with(container, fake_llm, reply="Конечно, расскажу")
    assert outcome.media is None and await records(container) == []
    assert "media" not in reply_detail(outcome)
    await say(container, world, "А сколько длится?")
    await run_reply(container)
    assert EARLIER not in context_of(fake_llm)


# ---- a decision to send: the record follows the generation -------------------------------------

async def test_a_decision_to_send_is_one_record_that_follows_the_photo_to_the_client(
        container, world, fake_llm, photos):
    port, _ = photos
    await say(container, world, "Покажи, как ты сейчас")
    outcome = await reply_with(container, fake_llm, reply="Сейчас утро, варю кофе", media=ASK)
    ((row, record),) = await records(container)
    assert (record.requested, record.decision, record.outcome, record.scene) == ("none", "send", QUEUED, SCENE)
    assert record.warning == "" and "Исход: запущена генерация" in row.text
    (job,) = await media_jobs(container)
    assert job.payload["decision_id"] == row.id
    assert reply_detail(outcome)["media"]["outcome"] == QUEUED

    result = await run_media_job(container)
    ((same, record),) = await records(container)
    assert same.id == row.id and (record.outcome, record.media_message_id) == (GENERATED, result["message_id"])
    assert "Исход: сгенерировано, отправляется" in same.text

    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(result["message_id"])
    ((same, record),) = await records(container)
    assert len(port.photos) == 1 and record.outcome == SENT and same.text.endswith("Исход: отправлено")
    await set_setting(container, "persona_media_gap_min", 0)
    await say(container, world, "класс")
    await run_reply(container)
    assert re.search(STAMP + "ты решила прислать фото; исход: отправлено\\.", context_of(fake_llm))


@pytest.mark.parametrize(("error", "outcome", "label"), [
    ("image: HTTP 403: {\"detail\":\"project lacks scope: llm:agent\"}", BROKER_REFUSED, "отказ Broker"),
    ("image job 7 error: quota_exhausted", FAILED, "ошибка генерации"),
])
async def test_a_failed_generation_closes_the_record_with_the_reason(
        container, world, fake_llm, photos, error, outcome, label):
    _, images = photos
    images.error = ImageError(error)
    await say(container, world, "Пришли фото")
    await reply_with(container, fake_llm, reply="Лови", media=ASK)
    await run_media_job(container)
    ((row, record),) = await records(container)
    assert (record.requested, record.outcome, record.detail) == ("photo", outcome, error)
    assert f"Исход: {label} ({error})" in row.text
    await say(container, world, "что-то не пришло")
    await run_reply(container)
    assert re.search(STAMP + "ты решила прислать фото; исход: не ушло, собеседник его не получил",
                     context_of(fake_llm))


async def test_a_send_that_cannot_be_queued_is_recorded_with_why(container, world, fake_llm):
    await say(container, world, "Здравствуйте")
    await reply_with(container, fake_llm, media=ASK)             # nothing is on offer out of the box
    assert NO_MEDIA in context_of(fake_llm)
    ((row, record),) = await records(container)
    assert (record.outcome, record.offered) == (UNAVAILABLE, ()) and await media_jobs(container) == []
    assert "Было доступно: ничего" in row.text and "медиа этого вида сейчас недоступно" in row.text


async def test_a_send_in_a_reply_that_ends_the_conversation_is_recorded_as_not_queued(
        container, world, fake_llm, photos):
    await say(container, world, "Покажи, как ты сейчас")
    await reply_with(container, fake_llm, media=ASK, handoff_to_operator=True)
    ((_, record),) = await records(container)
    assert record.outcome == ENDED and await media_jobs(container) == []


# ---- the owner asked ---------------------------------------------------------------------------

async def test_an_owner_request_she_refused_is_journaled_as_such_and_kept_for_the_next_reply(
        container, world, fake_llm):
    dialog_id = await owner_dialog(container, world, "Пришли кружочек")
    reason = "гуляю на улице, у меня консультации"
    outcome = await reply_with(container, fake_llm, reply="не могу сейчас, я на улице",
                               media={"kind": "video_note", "decision": "decline", "reason": reason})
    ((row, record),) = await records(container)
    assert row.dialog_id == dialog_id and record.owner and record.owner_request
    assert row.text.startswith("Решение медиа: запрос владельца, отказ модели\nПросили: кружочек\n")
    assert f"Причина: «{reason}»" in row.text
    assert row.error == "причина не подтверждается распорядком (на это время в распорядке записи нет)"
    assert reply_detail(outcome)["media"] == {
        "requested": "video_note", "kind": "video_note", "decision": "decline", "outcome": DECLINED,
        "summary": "запрос владельца, отказ модели", "owner_request": True, "warning": row.error}
    async with transaction(container.factory) as s:
        s.add(Message(dialog_id=dialog_id, direction=Direction.IN, author=Author.CLIENT,
                      text="а сейчас?", delivery=Delivery.SENT, external_id="own-2"))
    await run_reply(container)
    assert re.search(STAMP + f"владелец просил кружочек, ты отказала, причина: «{reason}»\\.",
                     context_of(fake_llm))
