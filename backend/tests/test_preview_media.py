"""Admin preview chat: voice notes, attachments and the persona's voice reply."""
from __future__ import annotations

import pytest

from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import VoiceMode
from sim.ports.llm import LLMError, LLMResult
from sim.ports.speech import SpeechError
from sim.services.preview_media import (
    MAX_BYTES,
    AttachmentError,
    PreviewAttachments,
    classify,
    message_text,
)
from tests.conftest import OWNER, decision_json, login
from tests.test_voice import FakeEncoder, FakeSpeech


@pytest.mark.parametrize("name,ctype,voice,expected", [
    ("voice.webm", "audio/webm;codecs=opus", True, "voice"),
    ("song.mp3", "", False, "audio"),
    ("photo.JPG", "", False, "image"),
    ("scan", "image/png", False, "image"),
    ("notes.md", "", False, "file"),
    ("data.json", "application/json", False, "file"),
])
def test_classify(name, ctype, voice, expected):
    assert classify(name, ctype, voice=voice) == expected


def test_classify_rejects_unsupported_files():
    with pytest.raises(AttachmentError):
        classify("deck.pdf", "application/pdf", voice=False)


def test_a_voice_note_reads_as_its_transcript_and_files_are_labelled():
    assert message_text("voice", "voice.webm", "привет") == "привет"
    assert message_text("image", "a.jpg", "кот на диване") == "[изображение: кот на диване]"
    assert message_text("file", "a.txt", "текст").startswith("[файл a.txt]\n")


class Vision:
    def __init__(self, text: str = "чек на 500 грн", error: Exception | None = None) -> None:
        self.text, self.error = text, error
        self.calls: list[tuple[list, dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error is not None:
            raise self.error
        return LLMResult(text=self.text)


async def test_image_goes_to_broker_vision_as_a_data_url(container, fake_llm):
    vision = Vision()
    att = await PreviewAttachments(fake_llm, vision, container.media).read(b"\xff\xd8jpeg", "p.jpg", "image/jpeg")
    assert att.kind == "image" and att.text == "[изображение: чек на 500 грн]"
    messages, kwargs = vision.calls[0]
    assert kwargs == {"capability": "vision", "workflow": "preview-vision"}
    image = messages[0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")
    name = att.media_url.removeprefix("/api/media/")
    assert container.media.path(name).read_bytes() == b"\xff\xd8jpeg"


async def test_voice_is_transcribed_and_text_files_are_read(container, fake_llm):
    reader = PreviewAttachments(fake_llm, Vision(), container.media)
    voice = await reader.read(b"opus", "voice.webm", "audio/webm", voice=True)
    assert voice.kind == "voice" and voice.text == "привет из голосового"
    doc = await reader.read("строка 1\nстрока 2".encode(), "notes.txt", "text/plain")
    assert doc.text == "[файл notes.txt]\nстрока 1\nстрока 2"


async def test_errors_are_attachment_errors(container, fake_llm):
    reader = PreviewAttachments(fake_llm, Vision(error=LLMError("vision: down")), container.media)
    with pytest.raises(AttachmentError, match="Broker"):
        await reader.read(b"png", "a.png", "image/png")
    with pytest.raises(AttachmentError):
        await reader.read(b"", "a.txt", "text/plain")
    with pytest.raises(AttachmentError):
        await reader.read(b"x" * (MAX_BYTES + 1), "a.txt", "text/plain")


async def test_attachment_endpoint(api, world, fake_llm):
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    url = f"/api/personas/{world.persona.id}/preview/attachment"
    ok = await api.post(url, files={"file": ("voice.webm", b"opus", "audio/webm")}, data={"voice": "true"}, headers=h)
    assert ok.status_code == 200
    body = ok.json()
    assert body["kind"] == "voice" and body["text"] == "привет из голосового" and body["media_url"]
    bad = await api.post(url, files={"file": ("a.pdf", b"%PDF", "application/pdf")}, headers=h)
    assert bad.status_code == 422


async def voiced(container, world, mode: VoiceMode, speech: FakeSpeech) -> None:
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.voice_mode, persona.voice_id, persona.voice_style = mode, "Kore", "тепло"
    container.speech, container.encoder = speech, FakeEncoder()


async def test_preview_answers_a_voice_note_with_a_voice_note(api, container, world, fake_llm):
    speech = FakeSpeech()
    await voiced(container, world, VoiceMode.MIRROR, speech)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    url = f"/api/personas/{world.persona.id}/preview"
    fake_llm.replies = [decision_json(reply="Привет!"), decision_json(reply="Ага")]
    voiced_reply = (await api.post(url, json={"message": "привет", "voice_input": True}, headers=h)).json()
    assert voiced_reply["voice"]["media_url"].endswith(".ogg") and voiced_reply["voice"]["duration_s"] == 4.2
    assert speech.calls == [("Привет!", "Kore", "тепло", "voice-reply")]
    text_reply = (await api.post(url, json={"message": "ок"}, headers=h)).json()
    assert text_reply["voice"] is None                     # mirror: text answers text


async def test_preview_voice_failure_keeps_the_text(api, container, world, fake_llm):
    await voiced(container, world, VoiceMode.ALWAYS, FakeSpeech(error=SpeechError("speech: 503")))
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    fake_llm.replies = [decision_json(reply="Привет!")]
    body = (await api.post(f"/api/personas/{world.persona.id}/preview", json={"message": "hi"},
                           headers=h)).json()
    assert body["reply"] == "Привет!"
    assert body["voice"]["media_url"] is None and "503" in body["voice"]["error"]
