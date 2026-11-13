"""Outbound messages: queue, delivery through the channel port, manual mode."""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.audio import AudioError
from sim.adapters.channels.capabilities import RECORD_VOICE, Capability
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
from sim.services.chat_activity import showing
from sim.services.dialog_state import apply_message
from sim.services.guard import GuardOutcome
from sim.services.send_receipts import UNCERTAIN_SEND, SendReceipt, SendReceipts, SendUncertain
from sim.services.tracing import Step, Tracer
from sim.services.video_note import is_ai_video, label_delivered, owner_allowed, owner_telegram_id
from sim.services.voice import VOICE_NOTE, VoiceNote, VoiceRenderer

log = logging.getLogger(__name__)


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


def guard_fields(guard: GuardOutcome | None) -> dict[str, object]:
    if guard is None:
        return {}
    return {
        "guard_verdict": guard.verdict,
        "guard_violations": list(guard.violations),
        "guard_model": guard.model,
        "original_text": guard.original_text,
    }


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
    ) -> Message:
        """Queue an AI reply (`text` is the final, guard-approved text; `guard` is recorded). `final` = the last thing the AI says before stepping back (a
        refusal farewell, a hand-off note): it is still delivered although the dialog is
        paused / the client refused by the time it is sent. `voice` = deliver it as a voice
        note (rendered by the SEND job; `text` stays as its transcript)."""
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
        await self.jobs.enqueue(
            jobkinds.SEND,
            {"message_id": message.id, "final": final},
            # No artificial delay for the first message (generation already takes seconds); the
            # next short messages of one reply wait a typing pause (`delay_s`).
            now + timedelta(seconds=delay_s),
            dedup_key=send_key(message.id),
        )
        return message

    async def store_blocked(
        self, dialog: Dialog, result: LLMResult | None, guard: GuardOutcome
    ) -> Message:
        """The guard blocked the draft: keep it for the operator, never send it. The message does
        not touch the dialog preview / unread counters and has no SEND job."""
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.AI,
            text=guard.text,
            delivery=Delivery.CANCELLED,
            error=("blocked by the guard: " + "; ".join(guard.violations))[:500],
            created_at=self.clock(),
            model=result.model if result else None,
            provider=result.provider if result else None,
            tokens_in=result.tokens_in if result else None,
            tokens_out=result.tokens_out if result else None,
            cost_usd=result.cost_usd if result else None,
            **guard_fields(guard),
        )
        await self.messages.add(message)
        return message

    async def send_manual(
        self, dialog: Dialog, operator_id: int, text: str, *, request_id: str | None = None,
        media_kind: str | None = None, media_url: str | None = None,
    ) -> Message:
        """An operator writes in the dialog; repeated request IDs return the same message.

        The caller holds the dialog lock before invoking this method.
        """
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
        dialog.ai_paused = True
        dialog.needs_operator = False
        dialog.operator_id = operator_id
        await self.jobs.cancel_pending(jobkinds.REPLY, jobkinds.reply_key(dialog.id))
        await self.jobs.cancel_pending(jobkinds.FOLLOWUP, jobkinds.followup_key(dialog.id))
        await self.jobs.enqueue(
            jobkinds.SEND, {"message_id": message.id}, now, dedup_key=send_key(message.id)
        )
        return message

    async def deliver(self, message_id: int, *, final: bool = False) -> None:
        message = await self.messages.get(message_id)
        if message is None or message.delivery is Delivery.SENT:
            return
        dialog = await self.dialogs.get(message.dialog_id)
        if dialog is None:
            raise SendFailed("dialog not found")
        if message.delivery is Delivery.CANCELLED:
            return
        if (
            message.author is Author.AI
            and not final
            and (dialog.ai_paused or dialog.client.status is ClientStatus.REFUSED)
        ):
            # Queued while the AI was active; a human took over / the client refused since.
            message.delivery = Delivery.CANCELLED
            message.error = "cancelled: AI paused or client refused before sending"
            return
        if not await self.ai_video_gate(message, dialog):
            return
        channel = dialog.channel
        fingerprint = hashlib.sha256(json.dumps({
            "dialog_id": dialog.id, "channel_id": channel.id, "kind": channel.kind.value,
            "thread": dialog.external_thread_id, "text": message.text,
            "media_url": message.media_url,
            "voice": message.media_kind == VOICE_NOTE,
            "session_sha256": hashlib.sha256((channel.session_enc or "").encode()).hexdigest(),
        }, sort_keys=True).encode()).hexdigest()
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

        note = await self.voice_note(message, dialog, port)
        attachment = self.attachment(message, port)
        gallery_media = self.gallery_media(message, port)
        await self.receipts.claim(message.id, fingerprint)
        async with self.send_step(channel, message, voice=note is not None) as step:
            try:
                if note is not None:
                    result = await port.send_voice(  # type: ignore[attr-defined]
                        dialog.external_thread_id, note.audio, note.duration_s,
                    )
                elif gallery_media is not None:
                    result = await self._send_gallery(
                        port, dialog.external_thread_id, gallery_media, message.text,
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

    async def ai_video_gate(self, message: Message, dialog: Dialog) -> bool:
        """Hard allowlist for generated video notes (sim.services.video_note), whoever queued
        the message: False = cancelled, it is not for this dialog. Raises SendFailed (retried)
        while the "Сгенерировано ИИ" label before it has not been delivered."""
        if not is_ai_video(message.media_url):
            return True
        if not owner_allowed(dialog, await owner_telegram_id(self.session)):
            message.delivery = Delivery.CANCELLED
            message.error = "cancelled: AI video notes go only to the allowlisted owner account"
            log.warning("message %s: AI video note refused for dialog %s", message.id, dialog.id)
            return False
        if not await label_delivered(self.session, message):
            raise SendFailed("the AI label before the video note is not delivered yet")
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
                        ambience=persona.voice_ambience,
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
            dialog = await self.dialogs.get(message.dialog_id)
            if dialog is not None:
                dialog.needs_operator = True
