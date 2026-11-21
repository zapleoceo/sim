"""Turn a client's attachment into text the agent can read (docs/reply.md, «Распознавание»).

| attachment                 | how                                                      |
|----------------------------|----------------------------------------------------------|
| photo, sticker             | «Зрение», section [Фото]: 1-3 sentences in Russian       |
| video                      | «Зрение», section [Видео], the video inline              |
| voice, audio               | «Слух» (transcriber)                                     |
| video note («кружок»)      | «Слух» + «Зрение», section [Кружок]                      |
| PDF                        | «Зрение», section [PDF], fallback: plain text            |
| other files (docx, txt...) | plain text extracted by MediaToolsPort                   |

The agents (docs/agents.md) come from `agent_config`: model, key tier, max_tokens and the
vision prompt (one text, four sections; a missing section is its default).

The result goes to `media_asset.description` (`understanding=done`) or the error to
`understanding_error` (`failed`); a `media_understood` event is written either way. Every
Broker call is logged in `llm_call`. Model calls run outside any database transaction."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import select

from sim.db.models import Chat, MediaAsset, Message, Persona
from sim.db.session import transaction
from sim.domain.agent_prompts import (
    VISION_PDF,
    VISION_PHOTO,
    VISION_VIDEO,
    VISION_VIDEO_NOTE,
    vision_sections,
)
from sim.domain.agents import AgentConfig, AgentKey, agent_config
from sim.domain.enums import EventKind, LlmPurpose, MediaKind, MessageKind, UnderstandingStatus
from sim.events import append_event
from sim.ports import ChatMessage, ChatPart, PortError
from sim.services.deps import Deps
from sim.services.llm_log import describe_messages, logged_call

log = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
DOCUMENT_TEXT_LIMIT = 6_000


class UnderstandingError(Exception):
    pass


@dataclass(frozen=True)
class Attachment:
    media_id: int
    chat_id: int
    message_id: int
    message_kind: MessageKind
    kind: MediaKind
    mime: str
    file_name: str
    data: bytes
    vision: AgentConfig             # «Зрение»: its prompt holds one section per kind
    hearing: AgentConfig            # «Слух»

    def prompt(self, section: str) -> str:
        return vision_sections(self.vision.prompt)[section]


Strategy = Callable[["Understanding", Attachment], Awaitable[str]]


def pick_strategy(message_kind: MessageKind, kind: MediaKind, mime: str) -> str:
    """Name of the way to understand an attachment (keys of `Understanding.STRATEGIES`)."""
    if kind == MediaKind.PHOTO:
        return "image"
    if kind == MediaKind.AUDIO:
        return "transcribe"
    if kind == MediaKind.VIDEO:
        return "video_note" if message_kind == MessageKind.VIDEO_NOTE else "video"
    if mime.lower() == PDF_MIME:
        return "pdf"
    return "document"


class Understanding:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def understand(self, media_id: int) -> UnderstandingStatus | None:
        attachment = await self._load(media_id)
        if attachment is None:
            return None
        strategy = self.STRATEGIES[pick_strategy(attachment.message_kind, attachment.kind,
                                                 attachment.mime)]
        try:
            text = (await strategy(self, attachment)).strip()
            if not text:
                raise UnderstandingError("модель вернула пустой ответ")
        except (PortError, UnderstandingError) as exc:
            log.warning("media %s not understood: %s", media_id, exc)
            await self._save(attachment, None, str(exc) or type(exc).__name__)
            return UnderstandingStatus.FAILED
        await self._save(attachment, text, None)
        return UnderstandingStatus.DONE

    async def _load(self, media_id: int) -> Attachment | None:
        async with transaction(self.deps.sessions) as session:
            media = await session.get(MediaAsset, media_id)
            if media is None or media.understanding != UnderstandingStatus.PENDING:
                return None
            message = (await session.execute(
                select(Message).where(Message.media_id == media_id).order_by(Message.id).limit(1)
            )).scalar_one_or_none()
            if message is None:
                return None
            chat = await session.get(Chat, message.chat_id)
            persona = await session.get(Persona, chat.persona_id) if chat else None
            if persona is None:
                return None
            data = await self.deps.storage.get(media.storage_key)
            return Attachment(
                media_id=media.id, chat_id=message.chat_id, message_id=message.id,
                message_kind=message.kind, kind=media.kind, mime=media.mime,
                file_name=media.file_name, data=data,
                vision=agent_config(persona, AgentKey.VISION),
                hearing=agent_config(persona, AgentKey.TRANSCRIBE),
            )

    async def _save(self, attachment: Attachment, text: str | None, error: str | None) -> None:
        async with transaction(self.deps.sessions) as session:
            media = await session.get(MediaAsset, attachment.media_id, with_for_update=True)
            if media is None:
                return
            if text is not None:
                media.description = text
                media.understanding = UnderstandingStatus.DONE
                media.understanding_error = None
            else:
                media.understanding = UnderstandingStatus.FAILED
                media.understanding_error = error
            await append_event(
                session, chat_id=attachment.chat_id, kind=EventKind.MEDIA_UNDERSTOOD,
                message_id=attachment.message_id, media_id=attachment.media_id,
                data={"status": str(media.understanding), "error": error or ""},
            )

    # --- strategies -----------------------------------------------------------------------

    async def _vision(self, att: Attachment, section: str, data: bytes, mime: str,
                      purpose: LlmPurpose = LlmPurpose.VISION) -> str:
        vision = att.vision
        if not vision.model:
            raise UnderstandingError("не выбрана модель распознавания")
        messages = [ChatMessage(role="user", parts=[ChatPart(text=att.prompt(section)),
                                                    ChatPart(data=data, mime=mime)])]
        result = await logged_call(
            self.deps.sessions, purpose=purpose, model=vision.model,
            request={"messages": describe_messages(messages), "media_id": att.media_id},
            call=lambda: self.deps.llm.chat(model=vision.model, messages=messages,
                                            max_tokens=vision.max_tokens, label="understand",
                                            key_tier=vision.key_tier),
            chat_id=att.chat_id, key_tier=vision.key_tier,
        )
        return result.text

    async def _transcribe(self, att: Attachment) -> str:
        hearing = att.hearing
        if not hearing.model:
            raise UnderstandingError("не выбрана модель расшифровки")
        name = att.file_name or ("voice.ogg" if att.kind == MediaKind.AUDIO else "video.mp4")
        result = await logged_call(
            self.deps.sessions, purpose=LlmPurpose.TRANSCRIBE, model=hearing.model,
            request={"file_name": name, "mime": att.mime, "size_bytes": len(att.data),
                     "media_id": att.media_id},
            call=lambda: self.deps.transcriber.transcribe(audio=att.data, filename=name,
                                                          model=hearing.model,
                                                          key_tier=hearing.key_tier),
            chat_id=att.chat_id, key_tier=hearing.key_tier,
        )
        return result.text

    async def _image(self, att: Attachment) -> str:
        data, mime = await self.deps.tools.image_for_model(att.data, att.mime)
        return await self._vision(att, VISION_PHOTO, data, mime)

    async def _video(self, att: Attachment) -> str:
        return await self._vision(att, VISION_VIDEO, att.data, att.mime or "video/mp4")

    async def _video_note(self, att: Attachment) -> str:
        speech = ""
        seen = ""
        errors = []
        try:
            speech = (await self._transcribe(att)).strip()
        except (PortError, UnderstandingError) as exc:
            errors.append(str(exc))
        try:
            seen = (await self._vision(att, VISION_VIDEO_NOTE, att.data,
                                       att.mime or "video/mp4")).strip()
        except (PortError, UnderstandingError) as exc:
            errors.append(str(exc))
        if speech and "Говорит:" in seen:      # the transcript is the more exact copy of speech
            seen = seen.split("Говорит:", 1)[0].strip()
        parts = [f"Видно: {seen}" if seen else "", f"Говорит: {speech}" if speech else ""]
        text = " ".join(part for part in parts if part)
        if not text:
            raise UnderstandingError("; ".join(errors) or "пустой кружок")
        return text

    async def _pdf(self, att: Attachment) -> str:
        try:
            text = await self._vision(att, VISION_PDF, att.data, PDF_MIME,
                                      purpose=LlmPurpose.DOCUMENT)
            if text.strip():
                return text
        except (PortError, UnderstandingError) as exc:
            log.info("media %s: PDF via model failed (%s), reading text", att.media_id, exc)
        return await self._document(att)

    async def _document(self, att: Attachment) -> str:
        text = await self.deps.tools.document_text(att.data, att.mime, att.file_name)
        if not text or not text.strip():
            raise UnderstandingError("не удалось прочитать файл")
        text = " ".join(text.split())
        if len(text) > DOCUMENT_TEXT_LIMIT:
            text = text[:DOCUMENT_TEXT_LIMIT] + " …(дальше обрезано)"
        return text

    STRATEGIES: ClassVar[dict[str, Strategy]] = {
        "image": _image,
        "video": _video,
        "video_note": _video_note,
        "transcribe": _transcribe,
        "pdf": _pdf,
        "document": _document,
    }
