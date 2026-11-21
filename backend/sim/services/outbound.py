"""Outbound messages: queue, delivery through the channel port, manual mode."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.audio import AudioError
from sim.adapters.channels.capabilities import RECORD_VOICE, TYPING, Capability
from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import AdminUser, Channel, Dialog, Message, Persona
from sim.adapters.db.repositories.conversations import DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.media import MediaStore
from sim.clock import Clock, utcnow
from sim.connectors.spec import Connector
from sim.domain.enums import Author, ChannelKind, ClientStatus, Delivery, Direction, MediaKind
from sim.domain.voice import GEMINI_VOICES
from sim.ports.channel import SendResult
from sim.ports.llm import LLMResult
from sim.ports.speech import SpeechError
from sim.services import jobkinds, tracing
from sim.services.ai_media import AI_LABEL, is_ai_photo, is_ai_video
from sim.services.chat_activity import showing
from sim.services.dialog_state import apply_message
from sim.services.guard import GuardOutcome
from sim.services.humanize import TYPING_MAX_S
from sim.services.job_execution import require_live_execution
from sim.services.media_decisions import MediaDecisionLog
from sim.services.persona_ambience import voice_ambience
from sim.services.send_receipts import UNCERTAIN_SEND, SendReceipt, SendReceipts, SendUncertain
from sim.services.tracing import Step, Tracer
from sim.services.video_note import (
    CHAT_AUDIO_VALIDATION,
    ChatVideoTarget,
    owner_allowed,
    owner_telegram_id,
)
from sim.services.voice import VOICE_NOTE, VoiceNote, VoiceRenderer

log = logging.getLogger(__name__)

sleep = asyncio.sleep   # the typing pause between the parts of one reply (tests replace it)


class SendFailed(Exception):
    """Delivery did not succeed; the worker retries with backoff."""


send_key = jobkinds.send_key


async def cancel_queued_ai(session: AsyncSession, dialog_id: int, reason: str) -> int:
    """A human took over (or the AI was paused): AI replies still waiting for their
    send must not go out. Marks them cancelled and cancels their SEND jobs."""
    rows = (
        await session.scalars(
            select(Message).where(
                Message.dialog_id == dialog_id,
                Message.author == Author.AI,
                Message.delivery == Delivery.QUEUED,
            )
        )
    ).all()
    jobs = JobRepo(session)
    for message in rows:
        message.delivery = Delivery.CANCELLED
        message.error = reason[:500]
        await jobs.cancel_pending(jobkinds.SEND, send_key(message.id))
    return len(rows)


def media_caption(message: Message) -> str:
    """Generated photos are visibly disclosed; ordinary attachments keep their caption."""
    if is_ai_photo(message.media_url):
        return AI_LABEL
    return message.text


def guard_fields(guard: GuardOutcome | None) -> dict[str, object]:
    if guard is None:
        return {}
    return {
        "guard_verdict": guard.verdict,
        "guard_violations": list(guard.violations),
        "guard_model": guard.model,
    }


def send_fingerprint(message: Message, dialog: Dialog, channel: Channel) -> str:
    return hashlib.sha256(json.dumps({
        "dialog_id": dialog.id, "channel_id": channel.id, "kind": channel.kind.value,
        "thread": dialog.external_thread_id, "text": message.text,
        "media_url": message.media_url,
        "voice": message.media_kind == VOICE_NOTE,
        "session_sha256": hashlib.sha256((channel.session_enc or "").encode()).hexdigest(),
    }, sort_keys=True).encode()).hexdigest()


def apply_receipt(message: Message, channel: Channel, receipt: SendReceipt) -> None:
    message.delivery, message.error = Delivery.SENT, None
    if receipt.external_id:
        message.external_id = receipt.external_id
    channel.last_activity_at = max(channel.last_activity_at or receipt.sent_at, receipt.sent_at)


class OutboundService:
    def __init__(
        self,
        session: AsyncSession,
        registry: Mapping[ChannelKind, Connector],
        box: SecretBox,
        *,
        factory: async_sessionmaker[AsyncSession],
        clock: Clock = utcnow,
        voice: VoiceRenderer | None = None,
        media: MediaStore | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.session = session
        self.tracer = tracer
        self.registry = registry
        self.box = box
        self.clock = clock
        self.voice = voice
        self.media = media
        self.messages = MessageRepo(session)
        self.dialogs = DialogRepo(session)
        self.jobs = JobRepo(session)
        self.receipts = SendReceipts(factory, clock)

    async def queue_ai(
        self,
        dialog: Dialog,
        text: str,
        result: LLMResult | None,
        *,
        final: bool = False,
        guard: GuardOutcome | None = None,
        voice: bool = False,
        delay_s: float = 0.0,
        typing_s: float = 0.0,
    ) -> Message:
        """Queue an AI reply (`text` is the final, guard-approved text; `guard` is recorded). `final` = the last thing the AI says before stepping back (a
        refusal farewell, a hand-off note): it is still delivered although the dialog is
        paused / the client refused by the time it is sent. `voice` = deliver it as a voice
        note (rendered by the SEND job; `text` stays as its transcript). `typing_s` = a next part
        of one reply: its SEND job shows «печатает…» for that long before it sends."""
        now = self.clock()
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.AI,
            text=text,
            media_kind=VOICE_NOTE if voice else None,
            delivery=Delivery.QUEUED,
            created_at=now,
            model=result.model if result else None,
            provider=result.provider if result else None,
            tokens_in=result.tokens_in if result else None,
            tokens_out=result.tokens_out if result else None,
            cost_usd=result.cost_usd if result else None,
            **guard_fields(guard),
        )
        await self.messages.add(message)
        apply_message(dialog, message)
        payload: dict[str, Any] = {"message_id": message.id, "final": final}
        if typing_s > 0:
            payload["typing_s"] = typing_s
        await self.jobs.enqueue(
            jobkinds.SEND,
            payload,
            # No artificial delay for the first message (generation already takes seconds); a
            # next short message of one reply starts when the one before it is due (`delay_s`)
            # and then holds its own typing pause (`typing_s`) inside the job.
            now + timedelta(seconds=delay_s),
            dedup_key=send_key(message.id),
        )
        return message

    async def send_manual(
        self, dialog: Dialog, operator_id: int, text: str, *, request_id: str | None = None,
        media_kind: str | None = None, media_url: str | None = None,
    ) -> Message:
        """An operator writes in the dialog; repeated request IDs return the same message.

        The caller holds the dialog lock before invoking this method.
        """
        await self.session.flush()
        await self.session.refresh(dialog)
        if request_id:
            existing = await self.session.scalar(
                select(Message).where(Message.dialog_id == dialog.id,
                                      Message.client_request_id == request_id)
            )
            if existing is not None:
                if existing.text != text or existing.author is not Author.OPERATOR:
                    raise ValueError("request id already used for a different message")
                return existing
        now = self.clock()
        operator = await self.session.get(AdminUser, operator_id)
        await cancel_queued_ai(self.session, dialog.id, "cancelled: an operator took over")
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.OPERATOR,
            operator_id=operator.id if operator is not None else None,
            operator_name=operator.display_name[:120] if operator is not None else None,
            text=text,
            media_kind=media_kind,
            media_url=media_url,
            client_request_id=request_id,
            delivery=Delivery.QUEUED,
            created_at=now,
        )
        await self.messages.add(message)
        apply_message(dialog, message)
        # Human text, voice and attachments update context without changing an explicit pause
        # or resolving an outstanding handoff. Resuming AI is an explicit action.
        dialog.operator_id = operator_id
        await self.jobs.cancel_pending(jobkinds.REPLY, jobkinds.reply_key(dialog.id))
        await self.jobs.cancel_pending(jobkinds.FOLLOWUP, jobkinds.followup_key(dialog.id))
        await self.jobs.enqueue(
            jobkinds.SEND, {"message_id": message.id}, now, dedup_key=send_key(message.id)
        )
        return message

    async def deliver(
        self, message_id: int, *, final: bool = False, typing_s: float = 0.0,
        chat_video_target: ChatVideoTarget | None = None, media_decision_id: int | None = None,
        chat_audio_validation: str | None = None,
    ) -> None:
        message = await self.messages.get(message_id)
        if message is None or message.delivery is Delivery.SENT:
            return
        legacy_unbound = (chat_video_target is None and message.author is Author.AI
                          and message.media_kind == MediaKind.VIDEO_NOTE
                          and is_ai_video(message.media_url) and message.original_text is not None)
        outdated_audio = chat_video_target is not None and chat_audio_validation != CHAT_AUDIO_VALIDATION
        dialog = await self.dialogs.get(message.dialog_id)
        if legacy_unbound or outdated_audio:
            fingerprint = None
            if dialog is not None and (
                chat_video_target is None or chat_video_target == ChatVideoTarget.from_dialog(dialog)
            ):
                fingerprint = send_fingerprint(message, dialog, dialog.channel)
            previous = await self.receipts.previous(message.id, fingerprint)
            if previous is None:
                reason = ("chat video lacks current PCM validation" if outdated_audio
                          else "legacy chat video has no original route")
                await self.cancel_chat_video(message, reason, media_decision_id)
            elif dialog is not None:
                apply_receipt(message, dialog.channel, previous)
                await self.close_media_decision(message, ok=True, record_id=media_decision_id)
            return
        if dialog is None:
            raise SendFailed("dialog not found")
        if chat_video_target is not None:
            reason = await self.chat_video_rejection(chat_video_target, dialog)
            if reason is not None:
                await self.cancel_chat_video(message, reason, media_decision_id)
                return
        if self.called_off(message, dialog, final):
            return
        if chat_video_target is None and not await self.ai_video_gate(message, dialog):
            return
        channel = dialog.channel
        channel_session = channel.session_enc
        fingerprint = send_fingerprint(message, dialog, channel)
        previous = await self.receipts.previous(message.id, fingerprint)
        if previous is not None:
            apply_receipt(message, channel, previous)
            return
        connector = self.registry.get(channel.kind)
        if connector is None:
            raise SendFailed(f"no connector for {channel.kind}")
        try:
            session = self.box.decrypt_json(channel.session_enc)
            port = connector.build_port(channel.id, session)
        except Exception as exc:  # connector errors are heterogeneous; the job retries
            raise SendFailed(f"{type(exc).__name__}: {exc}"[:300]) from exc

        if typing_s > 0 and not await self.typed(message, dialog, port, typing_s, final):
            return
        note = await self.voice_note(message, dialog, port)
        attachment = self.attachment(message, port)
        gallery_media = self.gallery_media(message, port)
        if chat_video_target is not None and gallery_media is None:
            message.delivery = Delivery.FAILED
            message.error = "chat video file missing; nothing sent"
            await self.close_media_decision(message, ok=False, record_id=media_decision_id, reason=message.error)
            return
        if chat_video_target is None:
            await require_live_execution()
            await self.receipts.claim(message.id, fingerprint)
        async with self.send_step(channel, message, voice=note is not None) as step:
            if chat_video_target is not None:
                # All preparation is done. Check before claiming so a known cancellation
                # never creates an unresolved receipt for a transport call we did not make.
                reason = await self.current_chat_video_rejection(chat_video_target, channel_session)
                if reason is not None:
                    await self.cancel_chat_video(message, reason, media_decision_id)
                    return
                await require_live_execution()
                await self.receipts.claim(message.id, fingerprint)
                await require_live_execution()
                reason = await self.current_chat_video_rejection(chat_video_target, channel_session)
                if reason is not None:
                    # The receipt remains a fence. Only the message/decision records the
                    # proven abort; a crash before settlement must still fail closed.
                    await self.cancel_chat_video(
                        message, f"aborted before transport: {reason}", media_decision_id,
                    )
                    return
            try:
                if chat_video_target is None:
                    await require_live_execution()
                if note is not None:
                    result = await port.send_voice(  # type: ignore[attr-defined]
                        dialog.external_thread_id, note.audio, note.duration_s,
                    )
                elif gallery_media is not None:
                    result = await self._send_gallery(
                        port, dialog.external_thread_id, gallery_media, media_caption(message),
                    )
                elif attachment is not None:
                    data, filename, mime = attachment
                    result = await port.send_attachment(  # type: ignore[attr-defined]
                        dialog.external_thread_id, data, filename, mime, message.text,
                    )
                else:
                    result = await port.send_text(dialog.external_thread_id, message.text)
                if not result.ok:
                    # The common transport contract cannot prove that a failed request had no effect.
                    raise SendUncertain(UNCERTAIN_SEND)
                receipt = await self.receipts.acknowledge(
                    message.id, fingerprint, result.external_message_id,
                )
            except Exception:
                # Cancellation/crash also leaves the committed intent: recovery refuses a new send.
                # Never persist a transport exception containing credentials or message text.
                raise SendUncertain(UNCERTAIN_SEND) from None
            step.detail["chars"] = len(message.text or "")
        apply_receipt(message, channel, receipt)
        await self.close_media_decision(message, ok=True, record_id=media_decision_id)

    async def current_chat_video_rejection(
        self, target: ChatVideoTarget, channel_session: str | None,
    ) -> str | None:
        dialog = await self.session.get(Dialog, target.dialog_id, populate_existing=True)
        reason = await self.chat_video_rejection(target, dialog)
        if reason is None and dialog is not None and dialog.channel.session_enc != channel_session:
            return "original chat video channel session changed"
        return reason

    async def chat_video_rejection(self, target: ChatVideoTarget, dialog: Dialog | None) -> str | None:
        if dialog is None or target != ChatVideoTarget.from_dialog(dialog):
            return "original chat video route changed"
        if dialog.channel.is_archived:
            return "chat video channel archived"
        if dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED:
            return "AI paused or client refused"
        if not owner_allowed(dialog, await owner_telegram_id(self.session)):
            return "chat video recipient is no longer allowed"
        return None

    async def cancel_chat_video(self, message: Message, reason: str, record_id: int | None) -> None:
        message.delivery = Delivery.CANCELLED
        message.error = f"cancelled: {reason}"
        await self.close_media_decision(message, ok=False, record_id=record_id, reason=reason)

    async def close_media_decision(
        self, message: Message, *, ok: bool, record_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        """A generated photo or note the persona decided to send reached the client (or did not):
        the record of that decision gets its final outcome (sim.services.media_decisions)."""
        if is_ai_photo(message.media_url) or is_ai_video(message.media_url):
            await MediaDecisionLog(self.session, clock=self.clock).delivered(message, ok=ok, record_id=record_id, reason=reason)

    @staticmethod
    def called_off(message: Message, dialog: Dialog, final: bool) -> bool:
        """True when the message must not go out any more (it is marked cancelled if needed)."""
        if message.delivery is Delivery.CANCELLED:
            return True
        if (
            message.author is Author.AI
            and not final
            and (dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED)
        ):
            # Queued while the AI was active; a human took over / the client refused since.
            message.delivery = Delivery.CANCELLED
            message.error = "cancelled: AI paused or client refused before sending"
            return True
        return False

    async def typed(
        self, message: Message, dialog: Dialog, port: object, seconds: float, final: bool
    ) -> bool:
        """The pause before a next part of one reply, with «печатает…» («записывает голосовое…»
        for a voice note) shown for all of it and cleared when it ends. False = the message was
        called off during the pause (an operator took over, the AI was paused, the client
        refused) and must not be sent."""
        action = RECORD_VOICE if message.media_kind == VOICE_NOTE else TYPING
        async with showing(port, dialog.external_thread_id, action):
            await sleep(min(seconds, TYPING_MAX_S))
        for row in (message, dialog, dialog.client):
            await self.session.refresh(row)
        return not self.called_off(message, dialog, final)

    async def ai_video_gate(self, message: Message, dialog: Dialog) -> bool:
        """Hard allowlist for generated video notes (sim.services.video_note), whoever queued
        the message: False = cancelled, it is not for this dialog."""
        if not is_ai_video(message.media_url):
            return True
        if not owner_allowed(dialog, await owner_telegram_id(self.session)):
            message.delivery = Delivery.CANCELLED
            message.error = "cancelled: AI video notes go only to the allowlisted owner account"
            log.warning("message %s: AI video note refused for dialog %s", message.id, dialog.id)
            return False
        return True

    def try_port(self, channel: Channel) -> object | None:
        """The channel port for side signals (typing indicator), or None when it cannot be built.
        Never raises: such a signal must not fail the work it decorates."""
        connector = self.registry.get(channel.kind)
        if connector is None:
            return None
        try:
            return connector.build_port(channel.id, self.box.decrypt_json(channel.session_enc))
        except Exception as exc:
            log.debug("no port for channel %s: %s", channel.id, type(exc).__name__)
            return None

    def send_step(
        self, channel: Channel, message: Message, *, voice: bool = False
    ) -> AbstractAsyncContextManager[Step]:
        """The transport call as a step of the trace log (timing, outcome; never the text)."""
        name = f"send {'voice ' if voice else ''}{channel.kind.value}"
        if self.tracer is None:
            return nullcontext(Step(tracing.SEND, name))
        return self.tracer.step(tracing.SEND, name, channel_id=channel.id,
                                dialog_id=message.dialog_id, message_id=message.id)

    def attachment(self, message: Message, port: object) -> tuple[bytes, str, str] | None:
        if message.author is not Author.OPERATOR or message.media_kind not in ("image", "file"):
            return None
        if Capability.SEND_ATTACHMENT not in getattr(port, "capabilities", frozenset()):
            raise SendFailed("this channel cannot send attachments")
        url = message.media_url or ""
        if not url.startswith("/api/media/") or self.media is None:
            raise SendFailed("operator attachment is unavailable")
        name = url.removeprefix("/api/media/")
        path = self.media.path(name)
        if path is None:
            raise SendFailed("operator attachment is unavailable")
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return path.read_bytes(), name, mime

    async def voice_note(self, message: Message, dialog: Dialog, port: object) -> VoiceNote | None:
        """The rendered note for a message queued as voice, or None = send it as text.

        Voice is best-effort: when the channel cannot send voice, the persona has no valid
        voice any more, or the broker / ffmpeg fails, the client still gets the reply as text
        (the reason is logged; the message stops being marked as voice)."""
        if message.media_kind != VOICE_NOTE:
            return None
        if message.author is Author.OPERATOR:
            if Capability.SEND_VOICE not in getattr(port, "capabilities", frozenset()):
                raise SendFailed("this channel cannot send voice")
            name = (message.media_url or "").removeprefix("/api/media/")
            path = self.media.path(name) if self.media is not None else None
            if path is None or not (message.media_url or "").startswith("/api/media/"):
                raise SendFailed("operator voice file is unavailable")
            return VoiceNote(path.read_bytes(), None, None, message.media_url or "")
        if message.author is not Author.AI:
            return None
        persona = await self.session.get(Persona, dialog.client.persona_id)
        reason = None
        if self.voice is None:
            reason = "voice rendering is not configured"
        elif Capability.SEND_VOICE not in getattr(port, "capabilities", frozenset()):
            reason = f"{dialog.channel.kind} cannot send voice"
        elif persona is None or persona.voice_id not in GEMINI_VOICES:
            reason = "persona has no Gemini voice"
        else:
            try:
                async with showing(port, dialog.external_thread_id, RECORD_VOICE):
                    note = await self.voice.render(
                        message.text, voice=persona.voice_id, style=persona.voice_style,
                        ambience=voice_ambience(persona.voice_ambience, persona.routine,
                                                persona.timezone, self.clock()),
                    )
            except (SpeechError, AudioError) as exc:
                reason = f"{type(exc).__name__}: {exc}"[:300]
            else:
                message.media_url = note.media_url
                if note.cost_usd:
                    message.cost_usd = (message.cost_usd or 0.0) + note.cost_usd
                return note
        log.warning("message %s goes out as text, not voice: %s", message.id, reason)
        message.media_kind = None
        return None

    def gallery_media(self, message: Message, port: object) -> tuple[bytes, str, str] | None:
        """A photo or video_note from the persona gallery, or None."""
        if message.media_kind not in (MediaKind.PHOTO, MediaKind.VIDEO_NOTE):
            return None
        url = message.media_url or ""
        if not url.startswith("/api/media/") or self.media is None:
            return None
        name = url.removeprefix("/api/media/")
        path = self.media.path(name)
        if path is None:
            return None
        return path.read_bytes(), name, message.media_kind

    async def _send_gallery(
        self, port: object, thread: str, media: tuple[bytes, str, str], caption: str,
    ) -> SendResult:
        data, _name, kind = media
        if kind == MediaKind.PHOTO:
            if Capability.SEND_PHOTO not in getattr(port, "capabilities", frozenset()):
                raise SendFailed("this channel cannot send photos")
            return await port.send_photo(thread, data, caption)  # type: ignore[attr-defined]
        if kind == MediaKind.VIDEO_NOTE:
            if Capability.SEND_VIDEO_NOTE not in getattr(port, "capabilities", frozenset()):
                raise SendFailed("this channel cannot send video notes")
            return await port.send_video_note(thread, data)  # type: ignore[attr-defined]
        raise SendFailed(f"unsupported gallery media kind: {kind}")

    async def mark_failed(self, message_id: int, error: str) -> None:
        message = await self.messages.get(message_id)
        if message is not None and message.delivery is not Delivery.SENT:
            message.delivery = Delivery.FAILED
            message.error = error[:500]
            await self.close_media_decision(message, ok=False)
            dialog = await self.dialogs.get(message.dialog_id)
            if dialog is not None:
                dialog.needs_operator = True
