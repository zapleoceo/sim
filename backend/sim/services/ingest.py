"""Inbound messages: InboundMessage -> client / identity / dialog / message, dedup, queue reply."""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, replace
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.locks import NS_DIALOG, lock
from sim.adapters.db.models import Channel, Client, Dialog, Message
from sim.adapters.db.repositories.conversations import ClientRepo, DialogRepo, MessageRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, to_naive_utc, utcnow
from sim.domain.enums import Author, ClientStatus, Delivery, Direction
from sim.domain.funnel import Actor, status_on_inbound
from sim.ports.channel import InboundMessage
from sim.services import jobkinds, tracing
from sim.services.app_settings import AppSettingsService
from sim.services.dialog_reset import DialogReset, is_reset
from sim.services.dialog_state import apply_message
from sim.services.followup import FollowupService
from sim.services.funnel import FunnelService
from sim.services.media import MEDIA_KINDS, MEDIA_MAX_ATTEMPTS, VOICE_KINDS, placeholder_for
from sim.services.outbound import cancel_queued_ai
from sim.services.reply_journal import ReplyJournal
from sim.services.tracing import Tracer

log = logging.getLogger(__name__)

REF_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
ECHO_WINDOW = timedelta(minutes=10)
EDIT_PREFIX = "[изменено] "
# Channel handoff: the persona sends the client a link that carries the ref code (`#code` in a
# WhatsApp / Telegram-user text, `/start code` for the Telegram bot).
REF_IN_TEXT = re.compile(
    rf"(?:/start\s+|#)([{REF_ALPHABET}]{{8}})(?![{REF_ALPHABET}])", re.IGNORECASE
)
HANDOFF_PLACEHOLDER = "[перешёл по персональной ссылке из другого канала]"


def phone_variants(phone: str | None) -> list[str]:
    """Phones are stored as +<digits>, but older rows may lack the plus."""
    digits = re.sub(r"\D", "", phone or "")
    return [digits, f"+{digits}"] if len(digits) >= 7 else []


@dataclass(frozen=True)
class IngestResult:
    duplicate: bool = False
    message: Message | None = None
    dialog: Dialog | None = None
    client: Client | None = None
    reply_queued: bool = False
    status_before: ClientStatus | None = None
    reset: bool = False          # the message was `/reset`: the dialog was wiped, nothing stored


def new_ref_code() -> str:
    return "".join(secrets.choice(REF_ALPHABET) for _ in range(8))



class IngestService:
    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        *,
        debounce_s: int,
        clock: Clock = utcnow,
        tracer: Tracer | None = None,
    ) -> None:
        self.session = session
        self.tracer = tracer
        self.journal = ReplyJournal(tracer)
        self.debounce_s = debounce_s  # default; the owner setting "reply_debounce_s" wins
        self.clock = clock
        self.clients = ClientRepo(session)
        self.dialogs = DialogRepo(session)
        self.messages = MessageRepo(session)
        self.jobs = JobRepo(session)
        self.funnel = FunnelService(session)
        self.followups = FollowupService(session, clock=clock)

    async def ingest(self, channel: Channel, msg: InboundMessage) -> IngestResult:
        """Accept one inbound message, as an `inbound` step of the trace log (no text logged)."""
        if self.tracer is None:
            return await self.accept(channel, msg)
        async with self.tracer.step(tracing.INBOUND, f"inbound {channel.kind.value}",
                                    channel_id=channel.id) as step:
            result = await self.accept(channel, msg)
            step.status = tracing.OK if result.message is not None else tracing.SKIPPED
            step.links.update(
                dialog_id=result.dialog.id if result.dialog else None,
                client_id=result.client.id if result.client else None,
                message_id=result.message.id if result.message else None,
            )
            step.detail = {"duplicate": result.duplicate, "reply_queued": result.reply_queued,
                           "own": msg.from_me, "edit": msg.is_edit, "media": msg.media_kind,
                           "reset": result.reset}
        return result

    async def accept(self, channel: Channel, msg: InboundMessage) -> IngestResult:
        dialog = await self.dialogs.by_thread(channel.id, msg.external_thread_id)
        if dialog and msg.external_id and await self.messages.by_external(dialog.id, msg.external_id):
            return IngestResult(duplicate=True, dialog=dialog, client=dialog.client)
        if is_reset(msg.text) and not msg.media_kind and not msg.is_edit:
            return await self.reset(dialog)
        if msg.from_me:
            return await self.ingest_own(dialog, msg)
        if msg.is_edit:
            return await self.ingest_edit(dialog, msg)

        if dialog:
            client = dialog.client
        else:
            client, msg = await self.resolve_client(channel, msg)
        if dialog is None:
            dialog = await self.dialogs.add(
                Dialog(
                    channel_id=channel.id,
                    external_thread_id=msg.external_thread_id,
                    client_id=client.id,
                )
            )
            dialog.channel, dialog.client = channel, client
        await lock(self.session, NS_DIALOG, dialog.id)  # see sim.adapters.db.locks
        text = (msg.text or "").strip() or (placeholder_for(msg.media_kind) if msg.media_kind else "")
        created_at = to_naive_utc(msg.occurred_at)
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.IN,
            author=Author.CLIENT,
            text=text,
            media_kind=msg.media_kind,
            external_id=msg.external_id,
            delivery=Delivery.SENT,
            created_at=created_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(message)
        except IntegrityError:  # a concurrent delivery of the same external_id won the race
            return IngestResult(duplicate=True, dialog=dialog, client=client)
        await self.queue_media(dialog, message, msg)

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
        if not queued and dialog.ai_paused:   # stored, but nobody will answer: say so
            await self.journal.paused(dialog.id, message.id, source="inbound")
        return IngestResult(
            message=message,
            dialog=dialog,
            client=client,
            reply_queued=queued,
            status_before=status_before,
        )

    async def reset(self, dialog: Dialog | None) -> IngestResult:
        """`/reset` in the chat (from the client, or typed on the connected account itself): wipe
        the dialog and its context; the command itself is not stored and gets no reply."""
        if dialog is None:              # nothing to wipe yet
            return IngestResult(reset=True)
        await lock(self.session, NS_DIALOG, dialog.id)
        wiped = await DialogReset(self.session).reset(dialog, dialog.client)
        log.info("dialog %s reset by /reset: %d messages wiped", dialog.id, wiped)
        return IngestResult(dialog=dialog, client=dialog.client, reset=True)

    # ---- pieces --------------------------------------------------------------------------
    async def resolve_client(
        self, channel: Channel, msg: InboundMessage
    ) -> tuple[Client, InboundMessage]:
        """The client for a NEW dialog. A lead stays with the persona it came to: a new identity
        is attached to an existing client of the SAME persona when its first message carries that
        client's ref code, or when the phone is the same; otherwise a new client is created.
        Returns the (possibly code-stripped) message."""
        known = await self.clients.by_identity(channel.id, msg.external_user_id)
        if known:
            return known, msg
        match = REF_IN_TEXT.search(msg.text or "")
        if match:
            owner = await self.clients.by_ref(match.group(1).lower())
            if owner is not None and await self.can_attach(owner, channel):
                stripped = (msg.text[: match.start()] + msg.text[match.end():]).strip()
                msg = replace(msg, text=stripped or (HANDOFF_PLACEHOLDER if not msg.media_kind else ""))
                return await self.attach(owner, channel, msg), msg
        for phone in phone_variants(msg.phone):
            owner = await self.clients.by_phone(channel.persona_id, phone)
            if owner is not None and await self.can_attach(owner, channel):
                return await self.attach(owner, channel, msg), msg
        return await self.find_or_create_client(channel, msg), msg

    async def can_attach(self, owner: Client, channel: Channel) -> bool:
        """Same persona only, and never onto a client who already has an identity on this very
        channel (that would be a different person)."""
        if owner.persona_id != channel.persona_id:
            return False
        return all(i.channel_id != channel.id for i in await self.clients.identities_of(owner.id))

    async def attach(self, client: Client, channel: Channel, msg: InboundMessage) -> Client:
        await self.clients.add_identity(client, channel.id, channel.kind, msg.external_user_id)
        if msg.phone and not client.phone:
            client.phone = msg.phone
        if msg.user_handle and not client.handle:
            client.handle = msg.user_handle.lstrip("@")[:120]
        for dialog in await self.dialogs.for_client(client.id):
            await self.followups.cancel(dialog.id)  # the client is talking to us elsewhere now
        log.info("client %s: identity on channel %s attached", client.id, channel.id)
        return client

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

    async def queue_media(self, dialog: Dialog, message: Message, msg: InboundMessage) -> None:
        """Downloading and transcribing is network work: it runs as a `media` job, so the
        webhook answers at once. The reply job waits for it (see ConversationService)."""
        if not msg.media_url or msg.media_kind not in MEDIA_KINDS:
            return
        await self.jobs.enqueue(
            jobkinds.MEDIA,
            {
                "message_id": message.id,
                "ref": msg.media_url,
                "kind": msg.media_kind,
                "transcribe": msg.media_kind in VOICE_KINDS and not (msg.text or "").strip(),
            },
            self.clock(),
            dedup_key=jobkinds.media_key(dialog.id, message.id),
            max_attempts=MEDIA_MAX_ATTEMPTS,
        )

    async def queue_reply(self, dialog: Dialog, client: Client) -> bool:
        """One reply job per dialog; a burst of messages pushes it back (debounce)."""
        if dialog.ai_paused or client.status is ClientStatus.REFUSED:
            return False
        settings = AppSettingsService(self.session, {"reply_debounce_s": self.debounce_s})
        run_at = self.clock() + timedelta(seconds=(await settings.load()).reply_debounce_s)
        key = jobkinds.reply_key(dialog.id)
        await self.jobs.upsert_pending(jobkinds.REPLY, {"dialog_id": dialog.id}, run_at, key)
        return True

    async def ingest_edit(self, dialog: Dialog | None, msg: InboundMessage) -> IngestResult:
        """The client edited an earlier message. The new text is kept in the dialog (marked, so
        the model and the operator see it is a correction) but is not a new turn: no unread, no
        status change, no new reply (the original message was already answered or queued)."""
        text = (msg.text or "").strip()
        if dialog is None or not text:
            return IngestResult(duplicate=True)
        message = Message(
            dialog_id=dialog.id,
            direction=Direction.IN,
            author=Author.CLIENT,
            text=f"{EDIT_PREFIX}{text}",
            external_id=msg.external_id,
            delivery=Delivery.SENT,
            created_at=to_naive_utc(msg.occurred_at),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(message)
        except IntegrityError:
            return IngestResult(duplicate=True, dialog=dialog, client=dialog.client)
        apply_message(dialog, message)
        return IngestResult(message=message, dialog=dialog, client=dialog.client)

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
