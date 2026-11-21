"""The persona's own photos and video notes: what she is offered, how she asks, what the prompt
is built from, the cache, failures and the Broker image client.
No real Broker or channel is used."""
from __future__ import annotations

import base64
import itertools
import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Dialog, Job, Message, Persona, PersonaMedia
from sim.adapters.db.session import transaction
from sim.adapters.llm.broker import BrokerLLM, generated_png, image_request
from sim.domain.enums import Author, Delivery, Direction, MediaKind
from sim.ports.channel import SendResult
from sim.ports.image import ImageError, ImageReference, ImageResult
from sim.services import jobkinds
from sim.services.ai_media import AI_LABEL, AI_PHOTO_PREFIX, MEDIA_URL, history_text, is_ai_photo
from sim.services.app_settings import BY_KEY, AppSettingsService
from sim.services.conversation import ConversationService, decision_fields, history_turns
from sim.services.media_decisions import (
    NEUTRAL_MEDIA_SCENE,
    QUEUED,
    REFUSED,
    UNAVAILABLE,
    MediaDecisionLog,
    general_media_request_kind,
)
from sim.services.outbound import media_caption
from sim.services.persona_media_tool import (
    NO_MEDIA,
    PHOTO_FAILED,
    PHOTO_WORKFLOW,
    Offer,
    ShotEnvironment,
    photo_key,
    pick_references,
    shot_prompt,
)
from sim.services.prompt import Decision, MediaAsk, parse_decision, parse_media
from sim.worker.handlers import HANDLERS
from tests.conftest import FakePort, decision_json, guard_json, inbound
from tests.media_image_fixtures import JPEG, JPEG_BLUE
from tests.test_conversation import run_reply
from tests.test_ingest import ingest

MODEL = "codex-sub/gpt-6.1-sol"
PNG = b"\x89PNG\r\n\x1a\n-generated"
SCENE = "я на кухне, варю кофе, в сером свитере"
ASK = {"kind": "photo", "scene": SCENE}
URL = "https://broker.test"


@dataclass
class PhotoPort(FakePort):
    """A channel that can send photos; records them with their captions."""
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_PHOTO})
    photos: list[tuple[str, bytes, str]] = field(default_factory=list)

    async def send_photo(self, external_thread_id: str, data: bytes, caption: str) -> SendResult:
        self.photos.append((external_thread_id, data, caption))
        return SendResult(ok=True, external_message_id=f"photo-{len(self.photos)}")


@dataclass
class FakeImages:
    error: Exception | None = None
    calls: list[dict] = field(default_factory=list)

    async def generate_image(self, prompt, *, model, references=(), workflow=None):
        self.calls.append({"prompt": prompt, "model": model, "references": list(references),
                           "workflow": workflow})
        if self.error is not None:
            raise self.error
        return ImageResult(image=PNG, model=model)


async def set_setting(container, key: str, value) -> None:
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set(key, value, None)


MESSAGE_IDS = itertools.count(1)


async def say(container, world, text: str, thread: str = "chat-1") -> None:
    """The client writes one more message (each with its own channel id)."""
    await ingest(container, world.channel.id, inbound(text, ext=f"pm-{next(MESSAGE_IDS)}", thread=thread))


async def add_photo(container, persona_id: int, data: bytes = JPEG, tags: list[str] | None = None) -> int:
    name = await container.media.save(data, "jpg")
    async with transaction(container.factory) as s:
        row = PersonaMedia(persona_id=persona_id, kind=MediaKind.PHOTO, storage_name=name, tags=tags or [])
        s.add(row)
        await s.flush()
        return row.id


@pytest.fixture
async def studio(container, world, connector) -> tuple[PhotoPort, FakeImages]:
    """Everything a photo needs: the model setting, a reference in the gallery, a channel that
    sends photos, an image port. The client has written the first message."""
    connector.port = PhotoPort()
    container.image = FakeImages()
    await set_setting(container, "persona_photo_model", MODEL)
    await add_photo(container, world.persona.id)
    await say(container, world, "Покажи, как ты сейчас")
    return connector.port, container.image


async def rows(container, model):
    async with container.factory() as s:
        return list((await s.scalars(select(model).order_by(model.id))).unique().all())


async def media_jobs(container) -> list[Job]:
    """The queued generations."""
    return [j for j in await rows(container, Job)
            if j.kind == jobkinds.PERSONA_MEDIA and j.status == "pending"]


async def run_media_job(container) -> dict:
    (job,) = await media_jobs(container)
    async with transaction(container.factory) as s:
        result = await HANDLERS[jobkinds.PERSONA_MEDIA].run(container, s, job.payload)
        (await s.get(Job, job.id)).status = "done"
    return result


def context_of(fake_llm) -> str:
    """The dynamic context of the last persona call."""
    return fake_llm.gen_calls[-1][0][-1]["content"]


# ---- how she asks --------------------------------------------------------------------------

def test_the_media_request_is_parsed_from_the_decision():
    decision = parse_decision(decision_json(media={"kind": " Photo ", "scene": "  на кухне\nс кофе "}))
    assert decision.media == MediaAsk(MediaKind.PHOTO, "на кухне с кофе")
    assert parse_decision(decision_json()).media is None
    assert parse_media({"kind": "video_note", "scene": "машу рукой"}) == MediaAsk(MediaKind.VIDEO_NOTE, "машу рукой")
    for junk in (None, "photo", {"kind": "video", "scene": "x"}, {"kind": "photo", "scene": " "}, {"scene": "x"}):
        assert parse_media(junk) is None
    assert len(parse_media({"kind": "photo", "scene": "я" * 2000}).scene) == 600


def test_the_guard_sees_the_media_request_only_when_there_is_one():
    assert "media" not in decision_fields(Decision(reply="привет"))
    asked = decision_fields(Decision(reply="привет", media=MediaAsk(MediaKind.PHOTO, SCENE)))
    assert asked["media"] == {"kind": "photo", "scene": SCENE}


def test_the_offer_brief_names_only_what_is_on_offer():
    both = Offer((MediaKind.PHOTO, MediaKind.VIDEO_NOTE)).brief()
    assert 'своё фото или свой кружочек' in both and '"photo" или "video_note"' in both
    assert '"media": {"kind"' in both and "не обещай" in both and "ТВОИ ТОЧНЫЕ ДАННЫЕ НА СЕЙЧАС" in both
    only = Offer((MediaKind.PHOTO,)).brief()
    assert "кружоч" not in only.split("приложить", 1)[1].split(".", 1)[0] and '"video_note"' not in only
    assert Offer().brief() == NO_MEDIA and "Не обещай" in NO_MEDIA


# ---- the prompt of the shot ------------------------------------------------------------------

def marina(**fields) -> Persona:
    defaults = {"routine": "пн-пт 06:30-07:15 пробежка вдоль реки\nежедневно 23:00-06:30 сплю"}
    return Persona(name="Марина", gender="female", timezone="Europe/Kyiv", birth_date=date(1981, 3, 2),
                   **{**defaults, **fields})



def test_shot_environment_uses_local_clock_without_location_or_routine():
    morning = ShotEnvironment.capture(marina().timezone, datetime(2026, 11, 2, 5))
    assert morning.payload() == {"at": "2026-11-02T05:00:00+00:00", "light": "утро"}
    assert ShotEnvironment.parse(morning.payload()) == morning
    assert ShotEnvironment.capture(marina().timezone, datetime(2026, 11, 2, 22, 30)).light == "ночь"


def test_the_shot_prompt_uses_saved_synthetic_lighting():
    persona = marina()
    now = datetime(2026, 11, 2, 5)
    photo = shot_prompt(persona, MediaAsk(MediaKind.PHOTO, SCENE), ShotEnvironment.capture(persona.timezone, now))
    assert "любительское фото" in photo and f"Сцена: {SCENE}" in photo
    assert "женщина, возраст 45" in photo and "на приложенных фото" in photo
    assert "условное освещение для синтетической сцены: утро" in photo
    assert "пробежка" not in photo and "фактическое место и занятие не подтверждены" in photo
    assert "без обнажённости" in photo
    later = ShotEnvironment.capture(persona.timezone, now + timedelta(minutes=10))
    assert photo == shot_prompt(persona, MediaAsk(MediaKind.PHOTO, SCENE), later)
    note = shot_prompt(persona, MediaAsk(MediaKind.VIDEO_NOTE, SCENE), later)
    assert "кружочек" in note and note != photo
    assert "мужчина" in shot_prompt(Persona(name="Олег", gender="male", timezone="UTC", routine=""),
                                    MediaAsk(MediaKind.PHOTO, SCENE), later)


def test_references_prefer_photos_tagged_like_the_scene_and_stay_stable():
    photos = [PersonaMedia(id=i, tags=tags) for i, tags in
              ((1, ["офис"]), (2, []), (3, ["Кухня", "кофе"]), (4, ["кухня"]), (5, None))]
    assert [p.id for p in pick_references(photos, SCENE)] == [3, 4, 1]
    assert [p.id for p in pick_references(photos, "гуляю в парке", limit=2)] == [1, 2]
    one, two = ImageReference(b"a", "image/jpeg"), ImageReference(b"b", "image/jpeg")
    assert photo_key(1, "p", MODEL, [one]) == photo_key(1, "p", MODEL, [one])
    assert len({photo_key(1, "p", MODEL, [one]), photo_key(2, "p", MODEL, [one]), photo_key(1, "q", MODEL, [one]),
                photo_key(1, "p", "codex-sub/other", [one]), photo_key(1, "p", MODEL, [two])}) == 5


# ---- what she is offered ---------------------------------------------------------------------

def test_the_photo_model_is_an_owner_setting_that_is_empty_by_default():
    model, gap = BY_KEY["persona_photo_model"], BY_KEY["persona_media_gap_min"]
    assert (model.kind, model.default, model.group) == ("model", "", gap.group)
    assert (gap.kind, gap.default, gap.min) == ("int", 30, 0)
    assert model.validate(MODEL) == MODEL


async def test_out_of_the_box_she_is_told_not_to_promise_media(container, world, fake_llm):
    await say(container, world, "Здравствуйте")
    fake_llm.replies = [decision_json(media=ASK)]
    assert (await run_reply(container)).status == "sent"
    assert NO_MEDIA in context_of(fake_llm) and '"scene"' not in context_of(fake_llm)
    assert await media_jobs(container) == []      # she asked anyway: nothing is queued


@pytest.mark.parametrize("missing", ["setting", "reference", "capability", "port"])
async def test_a_photo_is_offered_only_when_everything_it_needs_is_there(
        container, world, fake_llm, connector, studio, missing):
    if missing == "setting":
        await set_setting(container, "persona_photo_model", "")
    elif missing == "reference":
        async with transaction(container.factory) as s:
            for row in (await s.scalars(select(PersonaMedia))).all():
                await s.delete(row)
    elif missing == "capability":
        connector.port = FakePort()
    else:
        container.image = None
    await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm)


async def test_she_is_offered_a_photo_and_her_request_is_queued_after_the_text(container, world, fake_llm, studio):
    fake_llm.replies = [decision_json(reply="Вот так выгляжу с утра", media=ASK)]
    outcome = await run_reply(container)
    context = context_of(fake_llm)
    assert "МЕДИА: к этому ответу ты можешь приложить своё фото." in context and NO_MEDIA not in context
    assert outcome.status == "sent" and outcome.decision.media == MediaAsk(MediaKind.PHOTO, SCENE)
    (job,) = await media_jobs(container)
    (reference,) = await rows(container, PersonaMedia)
    assert ShotEnvironment.parse(job.payload["shot_environment"]) is not None
    assert {k: v for k, v in {**job.payload, "decision_id": 0}.items() if k != "shot_environment"} == {
        "dialog_id": outcome.message.dialog_id, "kind": "photo", "scene": SCENE,
        "decision_id": 0, "reference_ids": [reference.id],
    }
    assert job.max_attempts == 1 and job.run_at > outcome.message.created_at
    assert "media" in json.loads(fake_llm.guard_calls[-1][0][-1]["content"])["decision"]
    # no video note for a dialog that is not the allowlisted account
    assert "кружочек" not in context.split("приложить", 1)[1].split(".", 1)[0]


@pytest.mark.parametrize("text", [
    "Где ты?", "Что сейчас делаешь?", "Не присылай фото",
    "Пришли фото на кухне", "Пришли кружочек на том фоне, который мы обсуждали",
    "Она сказала: пришли фото", "Пришли фото или кружочек",
])
def test_general_media_request_does_not_infer_missing_context(text):
    assert general_media_request_kind(text) is None


@pytest.mark.parametrize(("text", "reply", "previous", "expected"), [
    ("Пришли фото, пожалуйста!", "Хорошо", "none", NEUTRAL_MEDIA_SCENE),
    ("Пришли фото на кухне", "Хорошо", "none", SCENE),
    ("Пришли фото", "Я на кухне", "none", SCENE),
    ("Пришли фото", "Хорошо", "batch", SCENE),
    ("Пришли фото", "Хорошо", "completed", SCENE),
    ("Пришли фото", "Хорошо", "trimmed", SCENE),
])
async def test_general_photo_preserves_conversation_context(
    container, world, fake_llm, studio, monkeypatch, text, reply, previous, expected,
):
    async with transaction(container.factory) as s:
        first = await s.scalar(select(Message).where(Message.direction == Direction.IN))
        first.text = text if previous == "none" else "Я на кухне"
    if previous == "completed":
        fake_llm.replies = [decision_json(reply="Поняла")]
        earlier = await run_reply(container)
        async with transaction(container.factory) as s:
            await container.outbound(s).deliver(earlier.message.id)
    if previous != "none":
        await say(container, world, text)
    if previous == "trimmed":
        load_memory = ConversationService.memory

        async def trimmed_memory(self, *args, **kwargs):
            recent, ctx = await load_memory(self, *args, **kwargs)
            return recent[-1:], replace(ctx, history_note="История усечена")

        monkeypatch.setattr(ConversationService, "memory", trimmed_memory)
    fake_llm.replies = [decision_json(reply=reply, media=ASK)]
    assert (await run_reply(container)).status == "sent"
    reviewed = json.loads(fake_llm.guard_calls[-1][0][-1]["content"])["decision"]
    assert reviewed["media"]["scene"] == expected
    (job,) = await media_jobs(container)
    assert job.payload["scene"] == expected


async def test_guard_rewrite_requires_a_fresh_model_decision(container, fake_llm, studio):
    async with transaction(container.factory) as s:
        first = await s.scalar(select(Message).where(Message.direction == Direction.IN))
        first.text = "Пришли фото"
    fake_llm.replies = [
        decision_json(reply="Хорошо", media=ASK),
        decision_json(reply="Я на кухне", media=ASK),
    ]
    fake_llm.guard_replies = [guard_json("rewrite", fixed="Я на кухне")]
    await run_reply(container)
    reviewed = [json.loads(call[0][-1]["content"])["decision"]["media"]["scene"]
                for call in fake_llm.guard_calls]
    assert reviewed == [NEUTRAL_MEDIA_SCENE]          # the rewrite goes out unreviewed
    (job,) = await media_jobs(container)
    assert job.payload["scene"] == SCENE


@pytest.mark.parametrize("disposition", ["decline", "later", "handoff"])
async def test_generic_request_does_not_open_media_when_existing_control_stops_it(
    container, world, fake_llm, studio, disposition,
):
    async with transaction(container.factory) as s:
        first = await s.scalar(select(Message).where(Message.direction == Direction.IN))
        first.text = "Пришли фото"
    if disposition in {"decline", "later"}:
        raw = decision_json(media={"kind": "photo", "decision": disposition, "reason": "Не сейчас"})
    else:
        raw = decision_json(media=ASK, handoff_to_operator=True, handoff_reason="Нужен оператор")
    fake_llm.replies = [raw, raw]
    await run_reply(container)
    assert not await media_jobs(container)


async def test_no_media_is_queued_when_the_reply_ends_the_conversation(container, world, fake_llm, studio):
    fake_llm.replies = [decision_json(media=ASK, handoff_to_operator=True)]
    await run_reply(container)
    assert await media_jobs(container) == []


async def test_priority_contact_rewrites_arbitrary_refusal_without_bypassing_guard(
        container, world, fake_llm, studio):
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().external_thread_id = "12345"
    await set_setting(container, "media_priority_telegram_ids", "12345")
    fake_llm.replies = [decision_json(reply="Не по настроению", media={
        "kind": "photo", "decision": "decline", "reason": "не по настроению"}),
        decision_json(reply="Вечерний балкон", media=ASK)]
    fake_llm.guard_replies = [json.dumps({"verdict": "block", "violations": [
        "Произвольный отказ вместо доступного допустимого медиа"], "fixed_reply": ""})]
    await run_reply(container)
    assert len(fake_llm.gen_calls) == 2 and len(await media_jobs(container)) == 1
    assert any("ПРЕДПОЧТЕНИЕ КОНТАКТА" in str(m["content"])
               for m in fake_llm.gen_calls[-1][0])
    checked = json.loads(fake_llm.guard_calls[-1][0][-1]["content"])
    assert checked["context"]["media_priority"] is True
    assert "Не отказывай по настроению" in checked["context"]["media_offer"]


async def test_priority_does_not_enable_missing_generator(container, world, fake_llm, studio):
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().external_thread_id = "12345"
    await set_setting(container, "media_priority_telegram_ids", "12345")
    await set_setting(container, "persona_photo_model", "")
    fake_llm.replies = [decision_json(reply="Генерация сейчас недоступна", media={
        "kind": "photo", "decision": "decline", "reason": "генератор недоступен"})]
    await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm) and await media_jobs(container) == []


async def test_other_contact_does_not_receive_priority_policy(container, world, fake_llm, studio):
    await set_setting(container, "media_priority_telegram_ids", "12345")
    await run_reply(container)
    assert "ПРЕДПОЧТЕНИЕ КОНТАКТА" not in context_of(fake_llm)


# ---- the job: generate, cache, send ----------------------------------------------------------

async def ask_for_a_photo(container, fake_llm) -> None:
    fake_llm.replies = [decision_json(reply="Сейчас утро, варю кофе", media=ASK)]
    await run_reply(container)


async def test_the_photo_is_generated_from_her_gallery_and_sent_with_disclosure(
        container, world, fake_llm, studio):
    port, images = studio
    await ask_for_a_photo(container, fake_llm)
    result = await run_media_job(container)
    (call,) = images.calls
    assert (call["model"], call["workflow"]) == (MODEL, PHOTO_WORKFLOW)
    assert call["references"] == [ImageReference(JPEG, "image/jpeg")]
    assert f"Сцена: {SCENE}" in call["prompt"] and "условное освещение" in call["prompt"]

    photo = (await rows(container, Message))[-1]
    assert result == {"outcome": "queued", "cached": False, "message_id": photo.id}
    assert (photo.text, photo.media_kind, photo.author) == ("", "photo", Author.AI)
    assert is_ai_photo(photo.media_url) and photo.original_text == SCENE and photo.model == MODEL
    assert container.media.path(photo.media_url.removeprefix(MEDIA_URL)).read_bytes() == PNG

    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(photo.id)
    assert port.photos == [("chat-1", PNG, AI_LABEL)]
    assert (await rows(container, Message))[-1].delivery is Delivery.SENT


async def test_delayed_worker_uses_planned_lighting_after_clock_and_routine_change(
    container, world, fake_llm, studio,
):
    _, images = studio
    container.clock = lambda: datetime(2026, 11, 2, 9)
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    light = job.payload["shot_environment"]["light"]
    container.clock = lambda: datetime(2026, 11, 2, 22)
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.routine = "ежедневно 00:00-23:59 PRIVATE_ROUTINE_FIXTURE"
        persona.timezone = "UTC"
    assert (await run_media_job(container))["outcome"] == "queued"
    assert f"условное освещение для синтетической сцены: {light}" in images.calls[0]["prompt"]
    assert "PRIVATE_ROUTINE_FIXTURE" not in images.calls[0]["prompt"]


@pytest.mark.parametrize("snapshot", [None, {"at": "2026-11-02T05:00:00", "light": "утро"},
                                      {"at": "2026-11-02T05:00:00+00:00", "light": "произвольный текст"}])
async def test_old_or_invalid_shot_environment_never_calls_provider(container, fake_llm, studio, snapshot):
    _, images = studio
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    payload = dict(job.payload)
    payload.pop("shot_environment")
    if snapshot is not None:
        payload["shot_environment"] = snapshot
    async with transaction(container.factory) as s:
        result = await HANDLERS[jobkinds.PERSONA_MEDIA].run(container, s, payload)
        ((_, decision),) = await MediaDecisionLog(s).recent(payload["dialog_id"], 10)
        assert decision.outcome == REFUSED and decision.detail == result["error"]
    assert result["outcome"] == "refused"
    assert not images.calls


@pytest.mark.parametrize("change", ["none", "scene", "kind", "refs", "dialog", "delayed", "clock_drift", "wrong_stamp"])
async def test_legacy_media_plan_is_recovered_only_from_matching_queued_decision(
    container, fake_llm, studio, change,
):
    _, images = studio
    planned = datetime(2026, 11, 2, 9)
    container.clock = lambda: planned
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    payload = dict(job.payload)
    payload.pop("shot_environment")
    async with transaction(container.factory) as s:
        updates = {"planned_light": None}
        if change == "clock_drift":
            # A clock jump between storing the plan and writing the journal.
            updates["planned_at_utc"] = (planned + timedelta(seconds=6)).isoformat() + "+00:00"
        elif change == "wrong_stamp":
            updates["planned_at_utc"] = "2026-11-02T09:00:00"
        await MediaDecisionLog(s).update(payload["decision_id"], **updates)
    if change == "delayed":
        container.clock = lambda: planned + timedelta(hours=2)
    if change in {"scene", "kind", "refs", "dialog"}:
        payload.update({
            "scene": {"scene": "another scene"},
            "kind": {"kind": "video_note"},
            "refs": {"reference_ids": [payload["reference_ids"][0] + 1]},
            "dialog": {"dialog_id": payload["dialog_id"] + 1},
        }[change])
    async with transaction(container.factory) as s:
        result = await HANDLERS[jobkinds.PERSONA_MEDIA].run(container, s, payload)
        ((_, decision),) = await MediaDecisionLog(s).recent(job.payload["dialog_id"], 10)
    if change in {"none", "delayed", "clock_drift"}:
        assert result["outcome"] == "queued" and len(images.calls) == 1
        assert "условное освещение для синтетической сцены: нейтральное" in images.calls[0]["prompt"]
        assert decision.planned_light == "нейтральное"
    else:
        assert result["outcome"] == "refused" and not images.calls
        assert decision.planned_light is None
        if change == "wrong_stamp":
            assert decision.outcome == REFUSED and decision.detail == result["error"]
        else:
            assert decision.outcome == QUEUED and not decision.detail


async def test_photo_generator_keeps_planned_reference_order(container, world, fake_llm, studio):
    _, images = studio
    preferred = await add_photo(container, world.persona.id, data=JPEG_BLUE, tags=["кофе"])
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    assert job.payload["reference_ids"][0] == preferred
    assert (await run_media_job(container))["outcome"] == "queued"
    assert images.calls[0]["references"] == [
        ImageReference(JPEG_BLUE, "image/jpeg"), ImageReference(JPEG, "image/jpeg"),
    ]


@pytest.mark.parametrize("invalid", ["duplicate", "cardinality", "type", "foreign", "corrupt", "mime", "oversize"])
async def test_invalid_planned_photo_fails_before_provider(container, fake_llm, studio, invalid):
    _, images = studio
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    (reference_id,) = job.payload["reference_ids"]
    payload = dict(job.payload)
    if invalid in {"duplicate", "cardinality", "type"}:
        payload["reference_ids"] = {
            "duplicate": [reference_id, reference_id],
            "cardinality": [reference_id, 1001, 1002, 1003],
            "type": [True],
        }[invalid]
    else:
        if invalid == "corrupt":
            name = await container.media.save(b"\xff\xd8\xff\xe0broken", "jpg")
        elif invalid == "mime":
            name = await container.media.save(JPEG, "png")
        elif invalid == "oversize":
            name = await container.media.save(JPEG + bytes(4 * 1024 * 1024), "jpg")
        async with transaction(container.factory) as s:
            reference = await s.get(PersonaMedia, reference_id)
            if invalid == "foreign":
                persona = Persona(name="Synthetic other persona")
                s.add(persona)
                await s.flush()
                reference.persona_id = persona.id
            else:
                reference.storage_name = name
    async with transaction(container.factory) as s:
        result = await HANDLERS[jobkinds.PERSONA_MEDIA].run(container, s, payload)
    assert result["outcome"] == "refused"
    assert not images.calls


async def test_deleted_planned_photo_is_not_replaced_from_gallery(container, world, fake_llm, studio):
    _, images = studio
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    (planned_id,) = job.payload["reference_ids"]
    replacement = await add_photo(container, world.persona.id, data=JPEG_BLUE)
    assert replacement != planned_id
    async with transaction(container.factory) as s:
        await s.delete(await s.get(PersonaMedia, planned_id))
    result = await run_media_job(container)
    assert result["outcome"] == "refused"
    assert not images.calls


async def test_the_same_request_takes_the_picture_from_the_cache(container, world, fake_llm, studio):
    _, images = studio
    container.clock = lambda: datetime(2026, 11, 2, 5, 0)     # one moment of her day: one prompt
    await set_setting(container, "persona_media_gap_min", 0)
    await ask_for_a_photo(container, fake_llm)
    first = await run_media_job(container)
    await say(container, world, "Ещё раз покажи")
    await ask_for_a_photo(container, fake_llm)
    again = await run_media_job(container)
    assert (first["cached"], again["cached"]) == (False, True) and len(images.calls) == 1
    first_photo, second_photo = [m for m in await rows(container, Message) if m.media_kind == "photo"]
    assert first_photo.media_url == second_photo.media_url


async def test_a_generated_photo_keeps_disclosure_even_for_operator_caption(container, world, studio):
    port, _ = studio
    name = await container.media.save_as(f"{AI_PHOTO_PREFIX}{'a' * 40}.png", PNG)
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        message = Message(dialog_id=dialog.id, direction=Direction.OUT, author=Author.OPERATOR,
                          text="смотри, это я", media_kind="photo", media_url=MEDIA_URL + name,
                          delivery=Delivery.QUEUED)
        s.add(message)
        await s.flush()
        message_id = message.id
        assert media_caption(message) == AI_LABEL
    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(message_id)
    assert port.photos == [("chat-1", PNG, AI_LABEL)]
    plain = Message(text="подпись оператора", media_kind="photo", media_url=MEDIA_URL + "abcdefgh1234.jpg")
    assert media_caption(plain) == "подпись оператора"


@pytest.mark.parametrize("error", [
    "image: HTTP 403: {\"detail\":\"project lacks scope: llm:agent\"}",
    "image job 7 error: quota_exhausted",
])
async def test_a_failed_generation_sends_nothing_and_leaves_the_reason_for_the_owner(
        container, world, fake_llm, studio, error):
    port, images = studio
    images.error = ImageError(error)
    await ask_for_a_photo(container, fake_llm)
    result = await run_media_job(container)
    assert result == {"outcome": "failed", "error": error}
    failed = (await rows(container, Message))[-1]
    assert (failed.text, failed.delivery, failed.error) == (PHOTO_FAILED, Delivery.FAILED, error)
    assert failed.media_url is None and port.photos == []
    assert not [j for j in await rows(container, Job) if j.kind == jobkinds.SEND and j.payload["message_id"] == failed.id]
    # the gap also covers a failure: the next reply does not offer media again
    await say(container, world, "Ну что там?")
    await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm)


async def test_after_a_photo_the_tool_rests_for_the_gap_and_then_comes_back(container, world, fake_llm, studio):
    await ask_for_a_photo(container, fake_llm)
    assert NO_MEDIA not in context_of(fake_llm)
    await say(container, world, "И ещё одно")
    await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm)                 # the job is still queued
    await run_media_job(container)
    await say(container, world, "А ещё?")
    await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm)                 # a photo was made a moment ago
    await set_setting(container, "persona_media_gap_min", 0)
    await say(container, world, "Теперь можно?")
    await run_reply(container)
    assert NO_MEDIA not in context_of(fake_llm)


async def test_an_owner_account_has_no_gap_between_media(container, world, fake_llm, studio):
    owner = "169510539"
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().external_thread_id = owner
    await set_setting(container, "instant_reply_telegram_ids", owner)
    await ask_for_a_photo(container, fake_llm)
    await say(container, world, "И ещё одно", owner)
    await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm)                 # one generation at a time still
    await run_media_job(container)
    await say(container, world, "А ещё?", owner)
    await run_reply(container)
    assert NO_MEDIA not in context_of(fake_llm)             # a photo a moment ago: no gap for the owner


async def test_running_owner_generation_blocks_a_second_media_job(container, world, fake_llm, studio):
    _, images = studio
    owner = "169510539"
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().external_thread_id = owner
    await set_setting(container, "instant_reply_telegram_ids", owner)
    await ask_for_a_photo(container, fake_llm)
    (job,) = await media_jobs(container)
    async with transaction(container.factory) as s:
        running = await s.get(Job, job.id)
        running.status = "running"
        running.attempts = 1
        running.locked_until = container.clock() + timedelta(seconds=90)

    await say(container, world, "Пришли ещё фото", owner)
    fake_llm.replies = [decision_json(reply="Всё ещё варю кофе", media=ASK)]
    outcome = await run_reply(container)
    assert NO_MEDIA in context_of(fake_llm)
    assert outcome.media is not None and outcome.media.outcome == UNAVAILABLE
    generations = [j for j in await rows(container, Job) if j.kind == jobkinds.PERSONA_MEDIA]
    assert [(j.id, j.status) for j in generations] == [(job.id, "running")]
    assert images.calls == []


async def test_the_job_does_nothing_once_a_human_took_over(container, world, fake_llm, studio):
    _, images = studio
    await ask_for_a_photo(container, fake_llm)
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().ai_paused = True
    assert (await run_media_job(container))["outcome"] == "skipped"
    assert images.calls == []


# ---- the conversation history ----------------------------------------------------------------

def test_generated_media_reads_as_a_marker_and_an_old_label_is_not_her_words():
    def out(text="", **fields):
        return Message(direction=Direction.OUT, author=Author.AI, text=text, delivery=Delivery.SENT, **fields)

    photo = out(AI_LABEL, media_url=f"{MEDIA_URL}{AI_PHOTO_PREFIX}{'a' * 40}.png", original_text=SCENE)
    note = out(media_url=f"{MEDIA_URL}aivn_{'b' * 40}.mp4")
    assert history_text(photo) == f"[ты отправила своё фото: {SCENE}]"
    assert history_text(note) == "[ты отправила свой кружочек]"
    assert history_text(out(AI_LABEL)) == "" and history_text(out("привет")) == "привет"
    client = Message(direction=Direction.IN, author=Author.CLIENT, text=AI_LABEL, delivery=Delivery.SENT)
    assert history_text(client) == AI_LABEL
    turns = history_turns([client, out("привет"), out(AI_LABEL), photo])
    assert [t.text for t in turns] == [AI_LABEL, "привет", "", f"[ты отправила своё фото: {SCENE}]"]


# ---- the Broker client -----------------------------------------------------------------------

async def no_sleep(_: float) -> None:
    return None


def broker() -> BrokerLLM:
    return BrokerLLM(URL, "key-1", sleep=no_sleep)


def done(files) -> dict:
    return {"job_id": 9, "status": "done", "text": "ok", "model": MODEL, "cost_usd": None, "files": files}


def test_the_image_request_uses_the_wording_the_broker_documents():
    text = image_request("Сцена: кухня")
    assert text.startswith("$imagegen Generate exactly one real PNG image.") and "Сцена: кухня" in text
    assert text.endswith("do not draw it with code or SVG.")


def test_the_generated_picture_is_the_first_png_with_content():
    b64 = base64.b64encode(PNG).decode()
    files = [{"path": "notes.txt", "content_b64": "eA=="}, {"path": "out/a.png", "content_b64": None, "expired": True},
             {"path": "out/generated-1-9e85.PNG", "content_b64": b64}, {"path": "out/b.png", "content_b64": "eA=="}]
    assert generated_png(files) == PNG
    assert generated_png([]) == b"" and generated_png(None) == b"" and generated_png([{"path": "x.png"}]) == b""


@respx.mock
async def test_broker_submits_an_image_job_with_references_and_takes_the_png():
    submit = respx.post(f"{URL}/v1/jobs").respond(
        202, json={"job_id": 9, "status": "pending", "poll_url": "/v1/jobs/9", "poll_after_s": 2})
    respx.get(f"{URL}/v1/jobs/9").mock(side_effect=[
        httpx.Response(200, json={"job_id": 9, "status": "pending", "poll_after_s": 5}),
        httpx.Response(200, json=done([{"path": "out/generated-1.png",
                                        "content_b64": base64.b64encode(PNG).decode()}])),
    ])
    result = await broker().generate_image(
        "Сцена: кухня", model=MODEL, references=[ImageReference(JPEG, "image/jpeg")], workflow=PHOTO_WORKFLOW)
    assert result == ImageResult(image=PNG, model=MODEL, cost_usd=None)
    request = submit.calls.last.request
    assert request.headers["X-Project-Key"] == "key-1"
    assert dict(request.url.params) == {"capability": "chat:fast", "workflow": PHOTO_WORKFLOW}
    body = json.loads(request.content)
    assert set(body) == {"model", "messages", "workflow"} and body["model"] == MODEL   # no no_shell, no max_tokens
    (message,) = body["messages"]
    text, image = message["content"]
    assert message["role"] == "user" and text == {"type": "text", "text": image_request("Сцена: кухня")}
    assert image == {"type": "image_url", "image_url": {
        "url": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()}}


@respx.mock
async def test_broker_without_the_scope_says_so():
    respx.post(f"{URL}/v1/jobs").respond(403, json={"detail": "project lacks scope: llm:agent"})
    with pytest.raises(ImageError, match="403.*project lacks scope: llm:agent"):
        await broker().generate_image("x", model=MODEL)


@pytest.mark.parametrize("final", [
    {"job_id": 9, "status": "error", "error": "quota_exhausted"},
    done([]),
    done([{"path": "out/generated-1.png", "content_b64": "not base64!"}]),
])
@respx.mock
async def test_broker_job_failures_are_image_errors(final):
    respx.post(f"{URL}/v1/jobs").respond(202, json={"job_id": 9, "poll_after_s": 1})
    respx.get(f"{URL}/v1/jobs/9").respond(200, json=final)
    with pytest.raises(ImageError):
        await broker().generate_image("x", model=MODEL)
