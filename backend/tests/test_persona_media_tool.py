"""The persona's own photos and video notes: what she is offered, how she asks, what the prompt
is built from, the cache, the "Сгенерировано ИИ" caption, failures and the Broker image client.
No real Broker or channel is used."""
from __future__ import annotations

import base64
import itertools
import json
from dataclasses import dataclass, field
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
from sim.services.conversation import decision_fields, history_turns
from sim.services.outbound import media_caption
from sim.services.persona_media_tool import (
    NO_MEDIA,
    PHOTO_FAILED,
    PHOTO_WORKFLOW,
    Offer,
    life_now,
    photo_key,
    pick_references,
    shot_prompt,
)
from sim.services.prompt import Decision, MediaAsk, parse_decision, parse_media
from sim.services.prompt_map import fixed_rules
from sim.worker.handlers import HANDLERS
from tests.conftest import FakePort, decision_json, inbound
from tests.test_conversation import run_reply
from tests.test_ingest import ingest

MODEL = "codex-sub/gpt-6.1-sol"
PNG = b"\x89PNG\r\n\x1a\n-generated"
JPEG = b"\xff\xd8\xff\xe0-reference"
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


async def say(container, world, text: str) -> None:
    """The client writes one more message (each with its own channel id)."""
    await ingest(container, world.channel.id, inbound(text, ext=f"pm-{next(MESSAGE_IDS)}"))


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



def test_life_now_is_her_local_weekday_day_part_and_routine():
    # Monday 2 November 2026, 05:00 UTC = 07:00 in Kyiv: the morning run
    assert life_now(marina(), datetime(2026, 11, 2, 5, 0)) == \
        "понедельник, утро; по распорядку сейчас: пробежка вдоль реки"
    assert life_now(marina(), datetime(2026, 11, 2, 12, 0)) == "понедельник, день; по распорядку сейчас: свободное время"
    assert life_now(marina(routine=""), datetime(2026, 11, 2, 22, 30)).startswith("вторник, ночь")


def test_the_shot_prompt_is_her_scene_her_looks_and_her_life_now():
    now = datetime(2026, 11, 2, 5, 0)
    photo = shot_prompt(marina(), MediaAsk(MediaKind.PHOTO, SCENE), now)
    assert "любительское фото" in photo and f"Сцена: {SCENE}" in photo
    assert "женщина, возраст 45" in photo and "на приложенных фото" in photo
    assert "понедельник, утро; по распорядку сейчас: пробежка вдоль реки" in photo
    assert "без обнажённости" in photo
    # no minutes or dates: the same ask later in the same part of her day is the same prompt
    assert photo == shot_prompt(marina(), MediaAsk(MediaKind.PHOTO, SCENE), now + timedelta(minutes=10))
    note = shot_prompt(marina(), MediaAsk(MediaKind.VIDEO_NOTE, SCENE), now)
    assert "кружочек" in note and note != photo
    assert "мужчина" in shot_prompt(Persona(name="Олег", gender="male", timezone="UTC", routine=""),
                                    MediaAsk(MediaKind.PHOTO, SCENE), now)


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
    assert NO_MEDIA in context_of(fake_llm) and '"media"' not in context_of(fake_llm)
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
    assert job.payload == {"dialog_id": outcome.message.dialog_id, "kind": "photo", "scene": SCENE}
    assert job.max_attempts == 1 and job.run_at > outcome.message.created_at
    assert "media" in json.loads(fake_llm.guard_calls[-1][0][-1]["content"])["decision"]
    # no video note for a dialog that is not the allowlisted account
    assert "кружочек" not in context.split("приложить", 1)[1].split(".", 1)[0]


async def test_no_media_is_queued_when_the_reply_ends_the_conversation(container, world, fake_llm, studio):
    fake_llm.replies = [decision_json(media=ASK, handoff_to_operator=True)]
    await run_reply(container)
    assert await media_jobs(container) == []


# ---- the job: generate, cache, send ----------------------------------------------------------

async def ask_for_a_photo(container, fake_llm) -> None:
    fake_llm.replies = [decision_json(reply="Сейчас утро, варю кофе", media=ASK)]
    await run_reply(container)


async def test_the_photo_is_generated_from_her_gallery_and_sent_with_the_ai_caption(
        container, world, fake_llm, studio):
    port, images = studio
    await ask_for_a_photo(container, fake_llm)
    result = await run_media_job(container)
    (call,) = images.calls
    assert (call["model"], call["workflow"]) == (MODEL, PHOTO_WORKFLOW)
    assert call["references"] == [ImageReference(JPEG, "image/jpeg")]
    assert f"Сцена: {SCENE}" in call["prompt"] and "по распорядку сейчас" in call["prompt"]

    photo = (await rows(container, Message))[-1]
    assert result == {"outcome": "queued", "cached": False, "message_id": photo.id}
    assert (photo.text, photo.media_kind, photo.author) == (AI_LABEL, "photo", Author.AI)
    assert is_ai_photo(photo.media_url) and photo.original_text == SCENE and photo.model == MODEL
    assert container.media.path(photo.media_url.removeprefix(MEDIA_URL)).read_bytes() == PNG

    async with transaction(container.factory) as s:
        await container.outbound(s).deliver(photo.id)
    assert port.photos == [("chat-1", PNG, AI_LABEL)]
    assert (await rows(container, Message))[-1].delivery is Delivery.SENT


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


async def test_a_generated_photo_never_goes_out_without_the_ai_caption(container, world, studio):
    port, _ = studio
    name = await container.media.save_as(f"{AI_PHOTO_PREFIX}{'a' * 40}.png", PNG)
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        message = Message(dialog_id=dialog.id, direction=Direction.OUT, author=Author.OPERATOR,
                          text="смотри, это я", media_kind="photo", media_url=MEDIA_URL + name,
                          delivery=Delivery.QUEUED)
        s.add(message)
        await s.flush()
        assert media_caption(message) == AI_LABEL
        await container.outbound(s).deliver(message.id)
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


async def test_the_job_does_nothing_once_a_human_took_over(container, world, fake_llm, studio):
    _, images = studio
    await ask_for_a_photo(container, fake_llm)
    async with transaction(container.factory) as s:
        (await s.scalars(select(Dialog))).unique().one().ai_paused = True
    assert (await run_media_job(container))["outcome"] == "skipped"
    assert images.calls == []


# ---- the conversation history ----------------------------------------------------------------

def test_generated_media_reads_as_a_marker_and_the_label_is_not_her_words():
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


def test_the_persona_page_shows_the_photo_rule_and_the_media_instruction_read_only():
    by_key = {rule.key: rule.text for rule in fixed_rules("Марина")}
    assert set(by_key) == {"call", "video_note", "ai_photo", "media_tool"}
    assert AI_LABEL in by_key["ai_photo"]
    assert Offer((MediaKind.PHOTO, MediaKind.VIDEO_NOTE)).brief() in by_key["media_tool"]
    assert NO_MEDIA in by_key["media_tool"]


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
