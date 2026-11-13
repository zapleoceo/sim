"""Telegram userbot adapter: a personal account over MTProto (Telethon), polled by the worker.

TelethonTransport is the only code that talks to Telethon; it hands plain dicts to
TelegramUserAdapter, which owns the policy (cursor, filtering, throttling, flood handling).

Reading never marks anything read (GetDialogs/GetHistory only) and the client does not
subscribe to updates, so the account keeps looking like a person who has not opened the chat.
Only private chats with real users are surfaced: no groups, channels, bots or Telegram's own
service account (777000)."""
from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon import errors as tg_errors
from telethon import functions as tl_functions
from telethon import types as tl_types
from telethon.events import NewMessage

from sim.adapters.channels._http import MEDIA_MAX_BYTES
from sim.adapters.channels.capabilities import RECORD_VOICE, Capability
from sim.adapters.channels.telegram import chunk_text
from sim.adapters.channels.tg_calls import TelegramCallBridge
from sim.clock import utcnow
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import InboundMessage, SendResult

logger = logging.getLogger(__name__)

MEDIA_PREFIX = "tgu-msg:"
SERVICE_USER_ID = 777000              # Telegram's own notifications account
DIALOG_LIMIT = 50                     # newest dialogs only: one cheap GetDialogs per poll
FIRST_SYNC_LIMIT = 20                 # messages read per dialog the first time we see it
FIRST_SYNC_WINDOW = timedelta(hours=24)
FOLLOW_LIMIT = 100                    # per dialog per poll once a cursor exists
SENT_LIMIT = 500

# Errors that mean "this session is dead", as opposed to a network blip.
DEAD_SESSION = (
    tg_errors.AuthKeyUnregisteredError, tg_errors.SessionRevokedError,
    tg_errors.SessionExpiredError, tg_errors.UserDeactivatedError,
    tg_errors.UserDeactivatedBanError, tg_errors.AuthKeyDuplicatedError,
)

Sleep = Callable[[float], Awaitable[None]]


def media_ref(peer_id: int, message_id: int) -> str:
    return f"{MEDIA_PREFIX}{peer_id}:{message_id}"


def parse_media_ref(ref: str) -> tuple[int, int]:
    if not ref.startswith(MEDIA_PREFIX):
        raise ValueError("not a tgu-msg ref")
    peer, _, msg = ref[len(MEDIA_PREFIX):].partition(":")
    try:
        return int(peer), int(msg)
    except ValueError:
        raise ValueError("malformed tgu-msg ref") from None


def to_naive_utc(dt: Any) -> datetime:
    if not isinstance(dt, datetime):
        return utcnow()
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def media_kind_of(m: Any) -> str | None:
    if getattr(m, "voice", None):
        return "voice"
    if getattr(m, "audio", None):
        return "audio"
    if getattr(m, "photo", None):
        return "image"
    if getattr(m, "video", None) or getattr(m, "video_note", None):
        return "video"
    return None


@dataclass
class TGUserState:
    """Per-channel runtime state, owned by the connector so it survives port rebuilds."""

    cursor: dict[int, int] = field(default_factory=dict)       # peer id -> last message id
    sent: OrderedDict[str, None] = field(default_factory=OrderedDict)   # our own send ids
    last_send: float | None = None                              # monotonic
    blocked_until: float = 0.0                                  # FloodWait on reads
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def remember_sent(self, external_id: str) -> None:
        self.sent[external_id] = None
        while len(self.sent) > SENT_LIMIT:
            self.sent.popitem(last=False)


class TelethonTransport:
    """One lazily connected Telethon client, reused across polls/sends of a channel.

    With `on_new_message` (the worker) the client also listens: every new incoming private
    message calls it, so the worker polls this channel at once instead of on the next sweep.
    The poll itself reads the message, so nothing is ingested twice and a missed event only
    delays the reply until the fallback sweep."""

    def __init__(
        self, client_factory: Callable[[], Any], on_new_message: Callable[[], None] | None = None,
        *, on_call: Callable[[Any], Awaitable[None]] | None = None,
        call_bridge: Callable[..., Any] = TelegramCallBridge,
    ) -> None:
        self._factory = client_factory
        self._client: Any | None = None
        self._connect_lock = asyncio.Lock()
        self._on_new = on_new_message
        self._on_call = on_call
        self._call_bridge = call_bridge
        self.calls: Any | None = None

    async def ready(self) -> Any:
        async with self._connect_lock:
            if self._client is None:
                self._client = self._factory()
                if self._on_new is not None:
                    self._client.add_event_handler(self.new_message, NewMessage(incoming=True))
            if not self._client.is_connected():
                await self._client.connect()
            if self._on_call is not None and self.calls is None:
                await self.attach_calls(self._client)
            return self._client

    async def attach_calls(self, client: Any) -> None:
        """Voice calls ride the same client. A failure (no native library, an account that
        cannot call) only disables calls: messaging must keep working."""
        assert self._on_call is not None
        try:
            bridge = self._call_bridge(client, self._on_call)
            await bridge.start()
        except Exception as exc:
            logger.warning("telegram_user calls disabled: %s: %s", type(exc).__name__,
                           str(exc)[:200])
            self._on_call = None
            return
        self.calls = bridge

    async def new_message(self, event: Any) -> None:
        """Telethon NewMessage handler: only private chats with people wake the poll."""
        if self._on_new is not None and getattr(event, "is_private", False):
            self._on_new()

    @staticmethod
    async def entity(client: Any, peer_id: int) -> Any:
        """StringSession keeps no entity cache: after a restart the access hash is unknown
        until dialogs are listed once."""
        try:
            return await client.get_input_entity(peer_id)
        except ValueError:
            await client.get_dialogs(limit=DIALOG_LIMIT)
            return await client.get_input_entity(peer_id)

    async def authorized(self) -> bool:
        client = await self.ready()
        try:
            return bool(await client.is_user_authorized())
        except DEAD_SESSION:
            return False

    async def dialogs(self) -> list[dict[str, Any]]:
        client = await self.ready()
        out: list[dict[str, Any]] = []
        async for d in client.iter_dialogs(limit=DIALOG_LIMIT):
            ent = d.entity
            uid = int(getattr(ent, "id", 0) or 0)
            if (
                not d.is_user or not uid or uid == SERVICE_USER_ID
                or getattr(ent, "bot", False) or getattr(ent, "deleted", False)
                or getattr(ent, "is_self", False)
            ):
                continue
            name = " ".join(p for p in (getattr(ent, "first_name", None),
                                        getattr(ent, "last_name", None)) if p) or None
            phone = str(getattr(ent, "phone", "") or "").lstrip("+")
            out.append({
                "peer_id": uid,
                "top_id": int(getattr(d.message, "id", 0) or 0),
                "unread": int(getattr(d, "unread_count", 0) or 0),
                "name": name,
                "username": getattr(ent, "username", None) or None,
                "phone": f"+{phone}" if phone else None,
            })
        return out

    async def messages(self, peer_id: int, *, min_id: int, limit: int) -> list[dict[str, Any]]:
        """Oldest first. min_id=0: the newest `limit`; min_id>0: the oldest `limit` after it."""
        client = await self.ready()
        entity = await self.entity(client, peer_id)
        raw: list[Any] = []
        async for m in client.iter_messages(entity, limit=limit, min_id=min_id,
                                            reverse=min_id > 0):
            raw.append(m)
        if min_id == 0:
            raw.reverse()
        return [
            {"id": int(m.id), "from_me": bool(m.out), "text": str(m.message or ""),
             "date": to_naive_utc(m.date), "media_kind": media_kind_of(m)}
            for m in raw
        ]

    async def send(self, peer_id: int, text: str) -> int:
        client = await self.ready()
        sent = await client.send_message(await self.entity(client, peer_id), text)
        return int(sent.id)

    async def send_voice(self, peer_id: int, audio: bytes, duration_s: float | None) -> int:
        """An OGG/Opus voice note (voice_note=True: shown as a voice bubble, not a file)."""
        from telethon.tl.types import DocumentAttributeAudio  # noqa: PLC0415

        client = await self.ready()
        file = io.BytesIO(audio)
        file.name = "voice.ogg"
        sent = await client.send_file(
            await self.entity(client, peer_id), file, voice_note=True,
            attributes=[DocumentAttributeAudio(duration=max(1, round(duration_s or 1)),
                                               voice=True)],
        )
        return int(sent.id)

    async def download(self, peer_id: int, message_id: int, max_bytes: int) -> bytes:
        client = await self.ready()
        entity = await self.entity(client, peer_id)
        message = await client.get_messages(entity, ids=message_id)
        if message is None:
            raise ValueError("message not found")
        size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        if size > max_bytes:
            raise ValueError(f"telegram file too large: {size} bytes")
        data = await client.download_media(message, file=bytes)
        if not data:
            raise ValueError("message has no downloadable media")
        if len(data) > max_bytes:
            raise ValueError(f"media exceeds {max_bytes} bytes")
        return bytes(data)

    async def close(self, *, logout: bool = False) -> None:
        try:
            if logout:
                await (await self.ready()).log_out()     # ends our session on Telegram
                self._client = None
                return
            client, self._client = self._client, None
            if client is not None:
                await client.disconnect()
        except Exception as exc:   # closing must never raise into unlink / shutdown
            logger.warning("telegram_user close failed: %s", type(exc).__name__)


class TelegramUserAdapter:
    kind: ChannelKind = ChannelKind.TELEGRAM_USER
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VOICE, Capability.SEND_ATTACHMENT,
                              Capability.CHAT_ACTION, Capability.MARK_READ, Capability.ACCEPT_CALL})

    def __init__(
        self,
        transport: TelethonTransport,
        state: TGUserState,
        *,
        min_send_interval_s: float = 3.0,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.t = transport
        self.s = state
        self.interval = min_send_interval_s
        self.sleep = sleep
        self.mono = monotonic
        self.now = now

    # --- inbound ---------------------------------------------------------------------------

    async def fetch_inbound(self) -> list[InboundMessage]:
        if self.mono() < self.s.blocked_until:
            return []
        try:
            dialogs = await self.t.dialogs()
        except tg_errors.FloodWaitError as exc:
            self.block(exc)
            return []
        out: list[InboundMessage] = []
        for d in dialogs:
            peer = int(d["peer_id"])
            known = self.s.cursor.get(peer, 0)
            if d["top_id"] <= known:
                continue                                  # nothing new in this dialog
            try:
                raw = await self.t.messages(
                    peer, min_id=known, limit=FOLLOW_LIMIT if known else FIRST_SYNC_LIMIT)
            except tg_errors.FloodWaitError as exc:
                self.block(exc)
                break
            self.s.cursor[peer] = max([m["id"] for m in raw], default=d["top_id"])
            out.extend(self.to_inbound(d, m) for m in self.select(raw, d, first_sync=not known))
        return out

    def select(
        self, raw: list[dict[str, Any]], dialog: dict[str, Any], *, first_sync: bool
    ) -> list[dict[str, Any]]:
        fresh = [m for m in raw if f"{dialog['peer_id']}:{m['id']}" not in self.s.sent]
        if not first_sync:
            return fresh
        # First sight of this dialog: answering months-old history would be wrong. Take what
        # is unread plus what arrived recently; and never our own messages (after a restart
        # their echo cannot be told from a human typing on the phone).
        incoming = [m for m in fresh if not m["from_me"]]
        unread = {m["id"] for m in incoming[-dialog["unread"]:]} if dialog["unread"] else set()
        horizon = self.now() - FIRST_SYNC_WINDOW
        return [m for m in incoming if m["id"] in unread or m["date"] >= horizon]

    @staticmethod
    def to_inbound(d: dict[str, Any], m: dict[str, Any]) -> InboundMessage:
        peer = int(d["peer_id"])
        kind = m["media_kind"]
        return InboundMessage(
            external_thread_id=str(peer),
            external_user_id=str(peer),
            text=m["text"],
            occurred_at=m["date"],
            external_id=f"{peer}:{m['id']}",
            user_name=d["name"],
            user_handle=f"@{d['username']}" if d["username"] else None,
            phone=d["phone"],
            media_kind=kind,
            media_url=media_ref(peer, m["id"]) if kind else None,
            from_me=m["from_me"],
        )

    def block(self, exc: tg_errors.FloodWaitError) -> None:
        seconds = int(getattr(exc, "seconds", 0) or 0)
        self.s.blocked_until = self.mono() + seconds
        logger.warning("telegram_user read flood wait: %ss", seconds)

    # --- outbound --------------------------------------------------------------------------

    async def send_chat_action(self, external_thread_id: str, action: str) -> None:
        """messages.setTyping: "печатает" or "записывает голосовое" for ~5 s (best effort)."""
        try:
            peer = int(external_thread_id)
            client = await self.t.ready()
            entity = await self.t.entity(client, peer)
            kind = (tl_types.SendMessageRecordAudioAction() if action == RECORD_VOICE
                    else tl_types.SendMessageTypingAction())
            await client(tl_functions.messages.SetTypingRequest(peer=entity, action=kind))
        except Exception as exc:
            logger.debug("telegram_user chat action failed: %s", type(exc).__name__)

    async def mark_read(self, external_thread_id: str, external_message_id: str) -> None:
        """messages.readHistory up to this message: the client sees it read (best effort).
        Inbound ids are "<peer>:<message id>"."""
        try:
            peer = int(external_thread_id)
            max_id = int(external_message_id.rpartition(":")[2])
            client = await self.t.ready()
            entity = await self.t.entity(client, peer)
            await client(tl_functions.messages.ReadHistoryRequest(peer=entity, max_id=max_id))
        except Exception as exc:
            logger.debug("telegram_user mark read failed: %s", type(exc).__name__)

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        """Chunks of 4096. A failure on the FIRST chunk is a failed send (the job retries);
        later chunks failing is a partial delivery, reported as sent so the retry does not
        duplicate the text. FloodWait on the first chunk is `flood_wait:<seconds>`."""
        try:
            peer = int(external_thread_id)
        except ValueError:
            return SendResult(ok=False, error="bad thread id")
        parts = chunk_text(text)
        first: str | None = None
        for index, part in enumerate(parts):
            try:
                mid = await self.send_throttled(peer, part)
            except tg_errors.FloodWaitError as exc:
                error = f"flood_wait:{int(getattr(exc, 'seconds', 0) or 0)}"
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
            else:
                ext = f"{peer}:{mid}"
                self.s.remember_sent(ext)        # every chunk: its echo is not a human turn
                first = first or ext
                continue
            if index == 0:
                return SendResult(ok=False, error=error)
            logger.warning("telegram_user partial delivery: chunk %d/%d failed (%s)",
                           index + 1, len(parts), error.split(":")[0])
            break
        return SendResult(ok=True, external_message_id=first)

    async def send_voice(
        self, external_thread_id: str, audio: bytes, duration_s: float | None
    ) -> SendResult:
        """One voice note, throttled like text; FloodWait is `flood_wait:<seconds>`."""
        try:
            peer = int(external_thread_id)
        except ValueError:
            return SendResult(ok=False, error="bad thread id")
        try:
            mid = await self.throttled(lambda: self.t.send_voice(peer, audio, duration_s))
        except tg_errors.FloodWaitError as exc:
            return SendResult(ok=False,
                              error=f"flood_wait:{int(getattr(exc, 'seconds', 0) or 0)}")
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        ext = f"{peer}:{mid}"
        self.s.remember_sent(ext)                # its echo is not a human turn
        return SendResult(ok=True, external_message_id=ext)

    async def send_attachment(
        self, external_thread_id: str, data: bytes, filename: str,
        mime: str, caption: str,
    ) -> SendResult:
        try:
            peer = int(external_thread_id)
        except ValueError:
            return SendResult(ok=False, error="bad thread id")
        async def send() -> int:
            client = await self.t.ready()
            file = io.BytesIO(data)
            file.name = filename
            sent = await client.send_file(
                await self.t.entity(client, peer), file, caption=caption[:1000],
            )
            return int(sent.id)
        try:
            mid = await self.throttled(send)
        except tg_errors.FloodWaitError as exc:
            return SendResult(ok=False,
                              error=f"flood_wait:{int(getattr(exc, 'seconds', 0) or 0)}")
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        ext = f"{peer}:{mid}"
        self.s.remember_sent(ext)
        return SendResult(ok=True, external_message_id=ext)

    async def send_throttled(self, peer: int, text: str) -> int:
        return await self.throttled(lambda: self.t.send(peer, text))

    async def throttled(self, send: Callable[[], Awaitable[int]]) -> int:
        """At most one send per account per `min_send_interval_s`, serialized."""
        async with self.s.lock:
            if self.s.last_send is not None:
                wait = self.interval - (self.mono() - self.s.last_send)
                if wait > 0:
                    await self.sleep(wait)
            try:
                return await send()
            finally:
                self.s.last_send = self.mono()

    # --- status / media --------------------------------------------------------------------

    async def session_status(self) -> SessionStatus:
        try:
            return SessionStatus.ACTIVE if await self.t.authorized() else SessionStatus.EXPIRED
        except Exception as exc:
            # A blip (network, timeout) must not read as expired: the owner would be sent
            # through a fresh SMS login for nothing.
            logger.warning("telegram_user health inconclusive (%s): treating as ok",
                           type(exc).__name__)
            return SessionStatus.ACTIVE

    async def download_media(self, ref: str) -> bytes:
        peer, message_id = parse_media_ref(ref)
        return await self.t.download(peer, message_id, MEDIA_MAX_BYTES)
