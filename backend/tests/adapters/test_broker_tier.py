"""`key_tier` (docs/agents.md, docs/broker.md): "paid" / "free" go to the Broker on every route,
"auto" and subscription models send nothing, so an older Broker gets the request it knows."""
from __future__ import annotations

import pytest
import respx

from sim.adapters.broker.chat import BrokerLlm, chat_body
from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.image import codex_image_body, images_job_body
from sim.adapters.broker.live import start_message
from sim.adapters.broker.speech import speech_body
from sim.adapters.broker.tier import KEY_TIER_FIELD, sent_tier, with_tier
from sim.adapters.broker.transcribe import BrokerTranscriber
from sim.adapters.broker.video import video_job_body
from sim.domain.agents import KeyTier
from sim.ports import ChatMessage, ChatPart
from tests.adapters.helpers import done, sent_json, submitted

MESSAGES = [ChatMessage(role="user", parts=[ChatPart(text="hi")])]


@pytest.mark.parametrize(("tier", "model", "sent"), [
    ("paid", "gemini/gemini-3.5-flash", "paid"),
    (KeyTier.FREE, "", "free"),
    (" Paid ", "x/y", "paid"),
    ("auto", "gemini/gemini-3.5-flash", None),
    ("", "gemini/gemini-3.5-flash", None),
    ("paid", "codex-sub/gpt-6.1-sol", None),
    ("free", "claude-sub/claude-sonnet-5-5", None),
])
def test_sent_tier(tier: str, model: str, sent: str | None) -> None:
    assert sent_tier(tier, model) == sent
    body = with_tier({"a": 1}, tier, model)
    assert body.get(KEY_TIER_FIELD) == sent and body["a"] == 1


def test_every_body_builder_carries_the_tier() -> None:
    chat = chat_body(model="m/x", messages=MESSAGES, json_schema=None, max_tokens=10,
                     workflow=None, label="", key_tier="paid")
    speech = speech_body(text="привет", voice="Kore", style="", model="m/tts", workflow="w",
                         key_tier="free")
    image = images_job_body(prompt="p", references=[], model="m/img", aspect_ratio="3:4",
                            workflow="w", key_tier="paid")
    video = video_job_body(prompt="p", first_frame=None, model="m/vid", duration_s=5,
                           aspect_ratio="9:16", resolution="720p", workflow="w",
                           key_tier="free")
    live = start_message(model="m/live", system="s", voice="", language="", greeting="",
                         max_seconds=0, workflow="w", key_tier="paid")
    assert [b[KEY_TIER_FIELD] for b in (chat, speech, image, video, live)] == [
        "paid", "free", "paid", "free", "paid"]


def test_auto_and_the_subscription_send_no_tier() -> None:
    chat = chat_body(model="m/x", messages=MESSAGES, json_schema=None, max_tokens=10,
                     workflow=None, label="")
    sub = chat_body(model="codex-sub/gpt-6.1-sol", messages=MESSAGES, json_schema=None,
                    max_tokens=10, workflow=None, label="", key_tier="paid")
    codex = codex_image_body(prompt="p", references=[], model="codex-sub/gpt-image")
    live = start_message(model="m", system="s", voice="", language="", greeting="",
                         max_seconds=0, workflow="w", key_tier="auto")
    for body in (chat, sub, codex, live):
        assert KEY_TIER_FIELD not in body


async def test_chat_sends_the_tier(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/jobs").mock(return_value=submitted())
    broker.get("/v1/jobs/7").mock(return_value=done(text="ok"))
    await BrokerLlm(http).chat(model="gemini/gemini-3.5-flash", messages=MESSAGES,
                               key_tier=KeyTier.PAID)
    assert sent_json(post)[KEY_TIER_FIELD] == "paid"


async def test_transcription_sends_the_tier_in_the_query(http: BrokerHttp,
                                                         broker: respx.MockRouter) -> None:
    post = broker.post("/v1/transcribe/keyed-jobs").mock(
        return_value=submitted(9, poll_url="/v1/transcribe/jobs/9"))
    broker.get("/v1/transcribe/jobs/9").mock(return_value=done(9, text="привет"))
    transcriber = BrokerTranscriber(http)
    await transcriber.transcribe(audio=b"OggS", filename="voice.ogg", key_tier="free")
    params = post.calls.last.request.url.params
    assert (params["workflow"], params[KEY_TIER_FIELD]) == ("sim-voice", "free")
    await transcriber.transcribe(audio=b"OggS", filename="voice.ogg")
    assert KEY_TIER_FIELD not in post.calls.last.request.url.params
