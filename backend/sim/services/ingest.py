"""Inbound messages: InboundMessage -> client / identity / dialog / message, dedup, queue reply."""
from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Channel, Client, Dialog, Message
from sim.adapters.db.repositories.conversations import ClientRepo, DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.media import MediaStore
from sim.clock import Clock, to_naive_utc, utcnow
from sim.domain.enums import Author, ClientStatus, Delivery, Direction
from sim.domain.funnel import Actor, status_on_inbound
from sim.ports.channel import InboundMessage
from sim.ports.llm import LLMError, LLMPort
from sim.services import jobkinds
from sim.services.dialog_state import apply_message
from sim.services.followup import FollowupService
from sim.services.funnel import FunnelService
from sim.services.outbound import cancel_queued_ai

log = logging.getLogger(__name__)

REF_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
VOICE_KINDS = ("audio", "voice")
ECHO_WINDOW = timedelta(minutes=10)
VOICE_FAILED = "[голосовое сообщение: не удалось распознать]"
MEDIA_PLACEHOLDERS = {
    "image": "[изображение]",
    "video": "[видео]",
    "audio": "[аудио]",
    "voice": "[голосовое сообщение]",
}

Downloader = Callable[[str], Awaitable[bytes]]


@dataclass(frozen=True)
class IngestResult:
    duplicate: bool = False
    message: Message | None = None
    dialog: Dialog | None = None
    client: Client | None = None
    reply_queued: bool = False
    status_before: ClientStatus | None = None


def new_ref_code() -> str:
    return "".join(secrets.choice(REF_ALPHABET) for _ in range(8))


def guess_suffix(kind: str | None) -> str:
    return {"image": "jpg", "video": "mp4", "audio": "ogg", "voice": "ogg"}.get(kind or "", "bin")


class IngestService:
    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        llm: LLMPort,
        media: MediaStore,
        *,
        debounce_s: int,
        download: Downloader,
        clock: Clock = utcnow,
    ) -> None:
        self.session = session
        self.llm = llm
        self.media = media
        self.debounce = timedelta(seconds=debounce_s)
        self.download = download
        self.clock = clock
        self.clients = ClientRepo(session)
        self.dialogs = DialogRepo(session)
        self.messages = MessageRepo(session)
        self.jobs = JobRepo(session)
        self.funnel = FunnelService(session)
        self.followups = FollowupService(session, clock=clock)

    async def ingest(self, channel: Channel, msg: InboundMessage) -> IngestResult:
        dialog = await self.dialogs.by_thread(channel.id, msg.external_thread_id)
        if dialog and msg.external_id and await self.messages.by_external(dialog.id, msg.external_id):
            return IngestResult(duplicate=True, dialog=dialog, client=dialog.client)
        if msg.from_me:
            return await self.ingest_own(dialog, msg)

        client = dialog.client if dialog else await self.find_or_create_client(channel, msg)
        if dialog is None:
            dialog = await self.dialogs.add(
                Dialog(
                    channel_id=channel.id,
                    external_thread_id=msg.external_thread_id,
                    client_id=client.id,
                )
            )
            dialog.channel, dialog.client = channel, client
        text, media_url = await self.resolve_content(msg)
        created_at = to_naive_utc(msg.occurred_at)
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.IN,
            author=Author.CLIENT,
            text=text,
            media_kind=msg.media_kind,
            media_url=media_url,
            external_id=msg.external_id,
            delivery=Delivery.SENT,
            created_at=created_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(message)
        except IntegrityError:  # a concurrent delivery of the same external_id won the race
            return IngestResult(duplicate=True, dialog=dialog, client=client)

        status_before = client.status
        new_status = status_on_inbound(client.status)
        if new_status != client.status:
            await self.funnel.set_status(client, new_status, Actor.SYSTEM, "client wrote again")
        if msg.user_name and not client.display_name:
            client.display_name = msg.user_name[:200]
        client.last_contact_at = max(created_at, client.last_contact_at or created_at)
        dialog.unread += 1
        dialog.followup_step = 0
        apply_message(dialog, message)
        await self.followups.cancel(dialog.id)
        channel.last_activity_at = self.clock()
        queued = await self.queue_reply(dialog, client)
        return IngestResult(
            message=message,
            dialog=dialog,
            client=client,
            reply_queued=queued,
            status_before=status_before,
        )

    # ---- pieces --------------------------------------------------------------------------
    async def find_or_create_client(self, channel: Channel, msg: InboundMessage) -> Client:
        client = await self.clients.by_identity(channel.id, msg.external_user_id)
        if client:
            return client
        handle = (msg.user_handle or "").lstrip("@")
        client = await self.clients.add(
            Client(
                persona_id=channel.persona_id,
                display_name=(msg.user_name or handle or msg.phone or "")[:200],
                avatar_url=msg.avatar_url or "",
                phone=msg.phone or "",
                handle=handle[:120],
                ref_code=await self.unique_ref(),
                last_contact_at=to_naive_utc(msg.occurred_at),
            )
        )
        await self.clients.add_identity(client, channel.id, channel.kind, msg.external_user_id)
        return client

    async def unique_ref(self) -> str:
        while True:
            code = new_ref_code()
            if await self.clients.by_ref(code) is None:
                return code

    async def resolve_content(self, msg: InboundMessage) -> tuple[str, str | None]:
        """Text for the dialog (voice transcribed) and the local media URL."""
        text = (msg.text or "").strip()
        media_url: str | None = None
        data: bytes | None = None
        if msg.media_url and msg.media_kind in (*VOICE_KINDS, "image"):
            try:
                data = await self.download(msg.media_url)
                name = self.media.save(data, guess_suffix(msg.media_kind))
                media_url = f"/api/media/{name}"
            except (httpx.HTTPError, ValueError, OSError) as exc:
                log.warning("media download failed (%s): %s", msg.media_kind, type(exc).__name__)
        if not text and msg.media_kind in VOICE_KINDS:
            text = await self.transcribe(data) if data else VOICE_FAILED
        if not text and msg.media_kind:
            text = MEDIA_PLACEHOLDERS.get(msg.media_kind, "[вложение]")
        return text, media_url

    async def transcribe(self, audio: bytes) -> str:
        try:
            result = await self.llm.transcribe(audio, "voice.ogg")
        except LLMError as exc:
            log.warning("transcription failed: %s", exc)
            return VOICE_FAILED
        return result.text.strip() or VOICE_FAILED

    async def queue_reply(self, dialog: Dialog, client: Client) -> bool:
        """One reply job per dialog; a burst of messages pushes it back (debounce)."""
        if dialog.ai_paused or client.status is ClientStatus.REFUSED:
            return False
        run_at = self.clock() + self.debounce
        key = jobkinds.reply_key(dialog.id)
        existing = await self.jobs.pending_by_key(key)
        if existing:
            existing.run_at = run_at
        else:
            await self.jobs.enqueue(jobkinds.REPLY, {"dialog_id": dialog.id}, run_at, dedup_key=key)
        return True

    async def ingest_own(self, dialog: Dialog | None, msg: InboundMessage) -> IngestResult:
        """A message sent from the account itself (phone app). A human took the wheel: the AI
        pauses on this dialog. Echoes of our own AI/operator sends are recognised and dropped."""
        if dialog is None or not (msg.text or "").strip():
            return IngestResult(duplicate=True)
        now = self.clock()
        text = msg.text.strip()
        for recent in await self.messages.recent(dialog.id, 6):
            if (
                recent.direction is Direction.OUT
                and recent.text.strip() == text
                and now - recent.created_at < ECHO_WINDOW
            ):
                if msg.external_id and not recent.external_id:
                    recent.external_id = msg.external_id
                return IngestResult(duplicate=True, dialog=dialog, client=dialog.client)
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.OUT,
            author=Author.OPERATOR,
            text=text,
            external_id=msg.external_id,
            delivery=Delivery.SENT,
            created_at=to_naive_utc(msg.occurred_at),
        )
        await self.messages.add(message)
        apply_message(dialog, message)
        dialog.ai_paused = True
        await cancel_queued_ai(self.session, dialog.id, "cancelled: the owner wrote from the phone")
        await self.jobs.cancel_pending(jobkinds.REPLY, jobkinds.reply_key(dialog.id))
        await self.followups.cancel(dialog.id)
        return IngestResult(message=message, dialog=dialog, client=dialog.client)
