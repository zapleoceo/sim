"""Fakes for the Telegram channel and calls: Telethon client / messages / events, the call
backend, the Broker live session, and the services the telegram process talks to. No network,
no native libraries."""
from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, ClientIdentity
from sim.domain.enums import MessageStatus
from sim.ports import InboundMessage, LiveEvent, OutboundMessage, Peer, PortError
from sim.telegram.calls.backend import CallGone, NoAnswer
from sim.telegram.calls.pcm import resample

# --- database --------------------------------------------------------------------------------


class _Ctx:
    """One use of the shared test session. Uses are serialized: concurrent tasks (two channels
    starting side by side) would otherwise begin two transactions on the same session, which
    a real pool of connections never does."""

    def __init__(self, owner: TestSessions) -> None:
        self.owner = owner

    async def __aenter__(self) -> AsyncSession:
        await self.owner.enter()
        return self.owner.session

    async def __aexit__(self, *exc: object) -> None:
        self.owner.leave()


class TestSessions:
    """An async_sessionmaker stand-in that hands out the test's rolled-back session (it is not
    closed between uses; `transaction()` commits SAVEPOINTs on it)."""

    __test__ = False

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.lock = asyncio.Lock()
        self.holder: asyncio.Task[Any] | None = None
        self.depth = 0

    def __call__(self) -> _Ctx:
        return _Ctx(self)

    async def enter(self) -> None:
        """Re-entrant for the task that holds it (nested uses in one task are fine)."""
        task = asyncio.current_task()
        if self.holder is not task:
            await self.lock.acquire()
            self.holder = task
        self.depth += 1

    def leave(self) -> None:
        self.depth -= 1
        if self.depth == 0:
            self.holder = None
            self.lock.release()


# --- Telethon objects ------------------------------------------------------------------------


@dataclass
class FakeUser:
    id: int = 1001
    first_name: str | None = "Дима"
    last_name: str | None = "Z"
    username: str | None = "dima"
    phone: str | None = "380501234567"
    access_hash: int | None = 555
    bot: bool = False
    deleted: bool = False
    is_self: bool = False


@dataclass
class FakeFile:
    mime_type: str | None = None
    size: int = 0
    name: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None


class FakeTgMessage:
    """A Telethon Message: `media` names which property is set (photo, voice, document...)."""

    def __init__(self, id: int = 10, text: str = "", *, media: str | None = None,
                 data: bytes = b"", file: FakeFile | None = None, reply_to: int | None = None,
                 web_preview: bool = False, delay: float = 0.0,
                 fail_download: bool = False) -> None:
        self.id = id
        self.message = text
        self.date = datetime(2026, 11, 21, 18, 0, tzinfo=UTC)
        self.reply_to_msg_id = reply_to
        self.web_preview = SimpleNamespace() if web_preview else None
        self.file = file or (FakeFile(size=len(data)) if media else None)
        self.data = data
        self.delay = delay
        self.fail_download = fail_download
        self.downloads = 0
        for attr in ("photo", "sticker", "voice", "video_note", "video", "gif", "audio",
                     "document"):
            setattr(self, attr, None)
        if media:
            setattr(self, media, SimpleNamespace(kind=media))
            if media != "photo":
                self.document = getattr(self, media)

    async def download_media(self, file: Any = None) -> bytes:
        self.downloads += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_download:
            raise ConnectionError("download broke")
        return self.data


class FakeEvent:
    def __init__(self, message: FakeTgMessage, sender: Any = None, *, is_private: bool = True,
                 chat_id: int | None = 1001) -> None:
        self.message = message
        self.sender = sender if sender is not None else FakeUser()
        self.is_private = is_private
        self.chat_id = chat_id

    async def get_sender(self) -> Any:
        return self.sender


@dataclass
class FakeDeletedEvent:
    deleted_ids: list[int]
    chat_id: int | None = None


class FakeTelethonClient:
    """Records what was sent; `errors` are raised by the next send calls, one each."""

    def __init__(self, *, authorized: bool = True, me: Any = None,
                 connect_error: Exception | None = None) -> None:
        self.authorized = authorized
        self.me = me or SimpleNamespace(id=777, username="marina", phone="380000000000",
                                        first_name="Марина")
        self.connect_error = connect_error
        self.connected = False
        self.disconnected: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.handlers: list[tuple[Any, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.requests: list[Any] = []
        self.read: list[tuple[Any, int]] = []
        self.errors: list[Exception] = []
        self.ids = itertools.count(500)
        self.entities: dict[int, Any] = {}
        self.random_ids: set[int] = set()
        self.lost_answers = 0

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        if not self.disconnected.done():
            self.disconnected.set_result(None)

    def drop(self) -> None:
        """The connection dies (Telethon gave up reconnecting)."""
        self.connected = False
        if not self.disconnected.done():
            self.disconnected.set_result(None)

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> Any:
        return self.me

    def add_event_handler(self, handler: Any, builder: Any) -> None:
        self.handlers.append((handler, builder))

    async def get_input_entity(self, user_id: int) -> Any:
        if user_id not in self.entities:
            raise ValueError("Could not find the input entity")
        return self.entities[user_id]

    async def get_entity(self, user_id: int) -> Any:
        return self.entities.get(user_id) or FakeUser(id=user_id)

    async def get_dialogs(self, limit: int = 0) -> list[Any]:
        return []

    # Sending: the messenger builds raw Send*Request with its own random_id; Telethon's private
    # helpers it uses are faked as identity parse / a dict "media" of the send_file options.

    async def _parse_message_text(self, text: str, parse_mode: Any) -> tuple[str, list[Any]]:
        return text, []

    async def _file_to_media(self, file: Any, **options: Any) -> tuple[None, Any, bool]:
        return None, {"name": file.name, "data": file.getvalue(), **options}, False

    def _deliver(self, request: Any) -> Any:
        """Like Telegram: a repeated random_id is refused, a new one makes a message.
        `lost_answers` deliveries succeed but their answer never comes (a timeout)."""
        from telethon import errors, types

        if request.random_id in self.random_ids:
            raise errors.RandomIdDuplicateError(request=request)
        self.random_ids.add(request.random_id)
        message_id = next(self.ids)
        options = getattr(request, "media", None)
        if isinstance(options, dict):
            entry = {"kind": "file", "caption": request.message, **options}
            media = SimpleNamespace(document=SimpleNamespace(
                attributes=options.get("attributes") or []))
        else:
            entry = {"kind": "message", "text": request.message}
            media = None
        self.sent.append({**entry, "entity": request.peer, "id": message_id,
                          "random_id": request.random_id})
        if self.lost_answers:
            self.lost_answers -= 1
            raise TimeoutError("the answer was lost")
        date = datetime(2026, 11, 21, 18, 0, tzinfo=UTC)
        if media is None:
            return types.UpdateShortSentMessage(out=True, id=message_id, pts=1, pts_count=1,
                                                date=date)
        return types.Updates(
            updates=[types.UpdateMessageID(id=message_id, random_id=request.random_id),
                     types.UpdateNewMessage(message=SimpleNamespace(id=message_id, media=media),
                                            pts=1, pts_count=1)],
            users=[], chats=[], date=date, seq=0)

    async def send_read_acknowledge(self, entity: Any, *, max_id: int) -> bool:
        self.read.append((entity, max_id))
        return True

    async def __call__(self, request: Any) -> Any:
        from telethon import functions

        if self.errors:
            raise self.errors.pop(0)
        messages = functions.messages
        if isinstance(request, messages.SendMessageRequest | messages.SendMediaRequest):
            return self._deliver(request)
        self.requests.append(request)
        return True


class FakeRuntime:
    def __init__(self, client: FakeTelethonClient | None = None) -> None:
        self.the_client = client

    def client(self, channel_id: int) -> Any:
        if self.the_client is None or not self.the_client.is_connected():
            raise PortError(f"telegram channel {channel_id} is not connected", retryable=True)
        return self.the_client


# --- services --------------------------------------------------------------------------------


@dataclass
class FakeInbox:
    ingested: list[InboundMessage] = field(default_factory=list)
    edits: list[tuple[int, Peer, str, str]] = field(default_factory=list)
    deletions: list[tuple[int, list[str]]] = field(default_factory=list)

    async def ingest(self, msg: InboundMessage) -> int | None:
        self.ingested.append(msg)
        return len(self.ingested)

    async def known(self, channel_id: int, peer: Peer, external_ids: list[str]) -> set[str]:
        stored = {m.external_id for m in self.ingested
                  if m.channel_id == channel_id and m.peer.external_id == peer.external_id}
        return stored & set(external_ids)

    async def edited(self, channel_id: int, peer: Peer, external_id: str, text: str) -> None:
        self.edits.append((channel_id, peer, external_id, text))

    async def deleted(self, channel_id: int, external_ids: list[str]) -> None:
        self.deletions.append((channel_id, external_ids))


class FakeChatResolver:
    """Inbox.resolve_chat over seeded rows: the identity by external id, its chat on the
    channel."""

    def __init__(self) -> None:
        self.peers: list[Peer] = []

    async def resolve_chat(self, session: AsyncSession, channel_id: int, peer: Peer) -> Chat:
        self.peers.append(peer)
        identity = (await session.scalars(select(ClientIdentity).where(
            ClientIdentity.external_id == peer.external_id))).one()
        return (await session.scalars(select(Chat).where(
            Chat.channel_id == channel_id, Chat.client_id == identity.client_id))).one()


@dataclass
class FakeCallContext:
    system: str = "Ты Марина."
    greeting: str = "Алло?"
    language: str = "ru"
    voice: str = "Vindemiatrix"
    model: str = "gemini-live"
    max_seconds: int = 30
    key_tier: str = "paid"
    speed: float = 1.0
    record: bool = False


class FakeContextBuilder:
    """`topics`: the `outgoing_topic` of each call context (None = an incoming call)."""

    def __init__(self, error: Exception | None = None,
                 context: FakeCallContext | None = None) -> None:
        self.error = error
        self.context = context or FakeCallContext()
        self.chats: list[int] = []
        self.topics: list[str | None] = []

    async def for_call(self, session: AsyncSession, chat_id: int, *,
                       outgoing_topic: str | None = None) -> FakeCallContext:
        self.chats.append(chat_id)
        self.topics.append(outgoing_topic)
        if self.error is not None:
            raise self.error
        return self.context


@dataclass
class FakeTasks:
    memory: list[int] = field(default_factory=list)
    checks: list[tuple[int, float]] = field(default_factory=list)   # (persona id, delay)
    replies: list[tuple[int, float]] = field(default_factory=list)  # (chat id, delay)

    async def phone_check(self, persona_id: int, delay_s: float = 0) -> None:
        self.checks.append((persona_id, delay_s))

    async def refresh_memory(self, chat_id: int) -> None:
        self.memory.append(chat_id)

    async def reply(self, chat_id: int, delay_s: float = 0) -> None:
        self.replies.append((chat_id, delay_s))


@dataclass
class FakeStorage:
    files: dict[str, bytes] = field(default_factory=dict)

    async def put(self, data: bytes, *, mime: str, prefix: str) -> str:
        key = f"{prefix}/{len(self.files)}"
        self.files[key] = data
        return key

    async def get(self, key: str) -> bytes:
        return self.files[key]

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)


class FakeMessenger:
    def __init__(self, *, error: Exception | None = None, external_id: str = "900") -> None:
        self.error = error
        self.external_id = external_id
        self.sent: list[tuple[int, OutboundMessage]] = []
        self.actions: list[tuple[int, str]] = []
        self.read: list[tuple[int, Peer, str]] = []

    async def send(self, channel_id: int, message: OutboundMessage) -> str | None:
        if self.error is not None:
            raise self.error
        self.sent.append((channel_id, message))
        return self.external_id

    async def set_typing(self, channel_id: int, peer: Peer, action: str) -> None:
        self.actions.append((channel_id, action))

    async def mark_read(self, channel_id: int, peer: Peer, external_id: str) -> None:
        self.read.append((channel_id, peer, external_id))


class FakeOutbox:
    """sim.services.outbox stand-in: flips the status and records the call."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str | None, datetime]] = []
        self.failed: list[tuple[int, str]] = []

    async def mark_sent(self, session: AsyncSession, message: Any, external_id: str | None,
                        at: datetime) -> None:
        message.status = MessageStatus.SENT
        message.external_id = external_id
        message.sent_at = at
        self.sent.append((message.id, external_id, at))

    async def mark_failed(self, session: AsyncSession, message: Any, error: str) -> None:
        message.status = MessageStatus.FAILED
        message.error = error
        self.failed.append((message.id, error))


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# --- calls -----------------------------------------------------------------------------------


class FakeIncomingCall:
    """`frames` are fed, then the caller hangs up when `hang_up` is set (or at once when
    `hang_up` is None)."""

    def __init__(self, *, frames: list[bytes] | None = None, hang_up: asyncio.Event | None = None,
                 answer_error: Exception | None = None, ended: bool = False,
                 peer: Peer | None = None, rate_hz: int = 48_000) -> None:
        self.external_id = "1001"
        self.rate_hz = rate_hz
        self.input = frames or []
        self.hang_up = hang_up
        self.answer_error = answer_error
        self._ended = ended
        self._peer = peer or Peer(external_id="1001", access_hash="555", name="Дима")
        self.log: list[str] = []
        self.played: list[bytes] = []

    @property
    def ended(self) -> bool:
        return self._ended

    async def peer(self) -> Peer:
        return self._peer

    async def decline(self) -> None:
        self.log.append("decline")
        self._ended = True

    async def answer(self) -> None:
        self.log.append("answer")
        if self.answer_error is not None:
            raise self.answer_error

    async def frames(self) -> AsyncIterator[bytes]:
        for frame in self.input:
            yield frame
        if self.hang_up is not None:
            await self.hang_up.wait()
        self._ended = True

    async def play(self, pcm: bytes) -> None:
        self.played.append(pcm)

    async def hangup(self) -> None:
        self.log.append("hangup")
        self._ended = True


class FakeOutgoingCall(FakeIncomingCall):
    """Her call (`OutgoingCall`): `ring` records the timeout, then the callee picks up, or
    raises NoAnswer(`no_answer`) / `ring_error`."""

    def __init__(self, *, no_answer: str | None = None, ring_error: Exception | None = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.no_answer = no_answer
        self.ring_error = ring_error
        self.rings: list[int] = []

    async def ring(self, timeout_s: int) -> None:
        self.rings.append(timeout_s)
        self.log.append("ring")
        if self.no_answer is not None:
            self._ended = True
            raise NoAnswer(self.no_answer)
        if self.ring_error is not None:
            raise self.ring_error


class FakeCallBackend:
    """`CallBackend` of one account: `dial` hands out `call` (or raises `error`)."""

    def __init__(self, call: FakeOutgoingCall | None = None,
                 error: Exception | None = None) -> None:
        self.call = call or FakeOutgoingCall()
        self.error = error
        self.dialed: list[Peer] = []

    async def start(self, on_call: Any) -> None:
        self.on_call = on_call

    async def dial(self, peer: Peer) -> FakeOutgoingCall:
        self.dialed.append(peer)
        if self.error is not None:
            raise self.error
        return self.call


class FakeTempo:
    """`Tempo` (sim/telegram/calls/tempo.py) without ffmpeg: keeps what a turn feeds and hands
    it to the sink at the call's rate on `flush`; `reset` drops it. `log` = the calls."""

    def __init__(self, speed: float, in_hz: int, out_hz: int, sink: Any) -> None:
        self.args = (speed, in_hz, out_hz)
        self.sink = sink
        self.pending = bytearray()
        self.log: list[str] = []

    async def feed(self, pcm: bytes) -> None:
        self.log.append("feed")
        self.pending += pcm

    async def flush(self) -> None:
        self.log.append("flush")
        if self.pending:
            self.sink(resample(bytes(self.pending), self.args[1], self.args[2]))
        self.pending.clear()

    async def reset(self) -> None:
        self.log.append("reset")
        self.pending.clear()


class FakeTempoFactory:
    """`TempoFactory` that records every tempo it made."""

    def __init__(self) -> None:
        self.made: list[FakeTempo] = []

    def __call__(self, speed: float, in_hz: int, out_hz: int, sink: Any) -> FakeTempo:
        tempo = FakeTempo(speed, in_hz, out_hz, sink)
        self.made.append(tempo)
        return tempo


class FakeLiveSession:
    """Emits `events`, then sets `emitted` and either stops (`end=True`, the model finished)
    or waits for `finish()`."""

    def __init__(self, events: list[LiveEvent], *, end: bool = False,
                 summary: dict[str, Any] | None = None) -> None:
        self.script = events
        self.end = end
        self.summary = summary if summary is not None else {"cost_usd": 0.12,
                                                            "model": "gemini-live"}
        self.emitted = asyncio.Event()
        self.finished = asyncio.Event()
        self.audio_in: list[bytes] = []

    async def send_audio(self, pcm16k: bytes) -> None:
        self.audio_in.append(pcm16k)

    async def events(self) -> AsyncIterator[LiveEvent]:
        for event in self.script:
            await asyncio.sleep(0)
            yield event
        self.emitted.set()
        if not self.end:
            await self.finished.wait()

    async def finish(self) -> dict[str, Any]:
        self.finished.set()
        return self.summary


class FakeLive:
    def __init__(self, session: FakeLiveSession | None = None,
                 error: Exception | None = None) -> None:
        self.session = session or FakeLiveSession([])
        self.error = error
        self.opened: list[dict[str, Any]] = []

    async def open(self, **kwargs: Any) -> FakeLiveSession:
        self.opened.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.session


def gone() -> CallGone:
    return CallGone("the caller hung up while the call was being picked up")


# --- QR login (sim/telegram/login.py) --------------------------------------------------------


class SessionPasswordNeededError(Exception):
    """Named like Telethon's: the login code matches errors by class name."""


class PasswordHashInvalidError(Exception):
    pass


class FakeQrLogin:
    """Telethon's QRLogin: `wait()` ends when the test calls `scan()`, `need_password()`,
    `expire()` or `fail()`; `recreate()` gives a new token."""

    def __init__(self) -> None:
        self.tokens = itertools.count(1)
        self.url = ""
        self.future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.renew()

    def renew(self) -> None:
        self.url = f"tg://login?token=T{next(self.tokens)}"
        self.future = asyncio.get_running_loop().create_future()

    async def recreate(self) -> None:
        self.renew()

    async def wait(self) -> Any:
        return await self.future

    def scan(self) -> None:
        self.future.set_result(SimpleNamespace(id=777))

    def need_password(self) -> None:
        self.future.set_exception(SessionPasswordNeededError("2FA"))

    def expire(self) -> None:
        self.future.set_exception(TimeoutError())

    def fail(self, exc: Exception) -> None:
        self.future.set_exception(exc)


class FakeLoginClient:
    """An unauthorized Telethon client for the QR login."""

    def __init__(self, *, twofa: str = "secret-2fa", me: Any = None,
                 connect_error: Exception | None = None) -> None:
        self.twofa = twofa
        self.me = me or SimpleNamespace(id=777, username="olya", phone="", first_name="Оля")
        self.connect_error = connect_error
        self.connected = False
        self.qr: FakeQrLogin | None = None
        self.passwords: list[str] = []
        self.session = SimpleNamespace(save=lambda: "SESSION-STRING")

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def qr_login(self) -> FakeQrLogin:
        self.qr = FakeQrLogin()
        return self.qr

    async def sign_in(self, *, password: str) -> Any:
        self.passwords.append(password)
        if password != self.twofa:
            raise PasswordHashInvalidError("wrong")
        return self.me

    async def get_me(self) -> Any:
        return self.me


class LoginClientFactory:
    """`(api_id, api_hash, proxy) -> FakeLoginClient`, recording what it was given."""

    def __init__(self, **client_kw: Any) -> None:
        self.client_kw = client_kw
        self.clients: list[FakeLoginClient] = []
        self.args: list[tuple[int, str, str]] = []

    def __call__(self, api_id: int, api_hash: str, proxy: str) -> FakeLoginClient:
        self.args.append((api_id, api_hash, proxy))
        client = FakeLoginClient(**self.client_kw)
        self.clients.append(client)
        return client

    @property
    def last(self) -> FakeLoginClient:
        return self.clients[-1]

    @property
    def qr(self) -> FakeQrLogin:
        assert self.last.qr is not None
        return self.last.qr
