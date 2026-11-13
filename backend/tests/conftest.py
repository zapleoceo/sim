"""Shared fixtures: sqlite file DB per test, fake LLM, fake connector, container, API client."""
from __future__ import annotations

import hashlib
import hmac
import itertools
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import AdminUser, Channel, Persona, Strategy
from sim.adapters.db.session import create_all, transaction
from sim.api.main import create_app
from sim.config import Settings
from sim.domain.enums import ChannelKind, Role, SessionStatus
from sim.ports.channel import ChannelPort, ConnectResult, InboundMessage, SendResult
from sim.ports.llm import LLMError, LLMResult
from sim.wiring import Container, build_container

PARTNER_SECRET = "partner-secret"
BOT_TOKEN = "123456:TEST-login-bot-token"
BOT_USERNAME = "sim_login_bot"
OWNER = (1001,)          # Telegram ids of the seeded admins: owner, operator, superadmin
OPERATOR = (1002,)
SUPERADMIN = (1003,)
_hash_counter = itertools.count(1)


def sign_widget(data: dict[str, Any], token: str = BOT_TOKEN) -> dict[str, Any]:
    """Add a valid Telegram Login Widget `hash` to the fields."""
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if v not in (None, ""))
    secret = hashlib.sha256(token.encode()).digest()
    return {**data, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()}


def widget_payload(tg_id: int, **over: Any) -> dict[str, Any]:
    """A fresh signed payload; auth_date differs per call so that every hash is unique."""
    data: dict[str, Any] = {
        "id": tg_id, "first_name": "Test", "auth_date": int(time.time()) - next(_hash_counter),
        **over,
    }
    return sign_widget(data)


def decision_json(**over: Any) -> str:
    import json

    base = {
        "reply": "Привет! Расскажите, что вас привело?",
        "stage": "engaged",
        "needs": [],
        "next_step": "узнать цели",
        "handoff_to_operator": False,
        "refused": False,
        "stop_contact": False,
    }
    return json.dumps({**base, **over}, ensure_ascii=False)


def guard_json(verdict: str = "ok", violations: list[str] | None = None, fixed: str = "") -> str:
    import json

    return json.dumps(
        {"verdict": verdict, "violations": violations or [], "fixed_reply": fixed},
        ensure_ascii=False,
    )


class FakeLLM:
    """Scripted LLMPort. `replies` items (generator calls) and `guard_replies` items (workflow
    "guard") are returned in order; an Exception item is raised. An empty guard queue answers
    "ok" from a model different from the generator's ("fake-guard")."""

    def __init__(self) -> None:
        self.guard_replies: list[str | Exception | LLMResult] = []
        self.replies: list[str | Exception | LLMResult] = []
        # The passportist (workflow "passport") has its own queue: its background job must not
        # eat the scripted persona replies. Empty queue: a fixed short passport.
        self.passport_replies: list[str | Exception | LLMResult] = []
        self.calls: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
        self.transcript = "привет из голосового"
        self.usage_calls: list[tuple[Any, Any]] = []
        self.usage_reply: dict[str, Any] | Exception = {
            "from": "", "to": "", "currency": "USD",
            "total": {"calls": 2, "cost_usd": 0.03, "tokens_in": 0, "tokens_out": 0},
            "by_capability": [{"capability": "speech", "calls": 2, "cost_usd": 0.03, "tokens_in": 0, "tokens_out": 0}],
            "by_day": [], "limits": {"daily_cap_usd": 5.0, "monthly_cap_usd": 50.0, "today_usd": 0.01, "month_usd": 0.03},
        }

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResult:
        self.calls.append((messages, kwargs))
        is_guard = str(kwargs.get("workflow", "")).endswith("guard")
        if kwargs.get("workflow") == "passport":
            item = self.passport_replies.pop(0) if self.passport_replies else "Кто он: тестовый клиент."
            if isinstance(item, Exception):
                raise item
            return item if isinstance(item, LLMResult) else LLMResult(text=item, model="fake-passport")
        queue = self.guard_replies if is_guard else self.replies
        item = queue.pop(0) if queue else (guard_json() if is_guard else decision_json())
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResult):
            return item
        return LLMResult(
            text=item, model="fake-guard" if is_guard else "fake-model", provider="fake", tokens_in=100, tokens_out=20,
            cached_tokens=50, cost_usd=0.002, latency_ms=7, job_id="1",
        )

    @property
    def gen_calls(self) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
        return [c for c in self.calls if not str(c[1].get("workflow", "")).endswith("guard")
                and c[1].get("workflow") != "passport"]

    @property
    def guard_calls(self) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
        return [c for c in self.calls if str(c[1].get("workflow", "")).endswith("guard")]

    async def transcribe(self, audio: bytes, filename: str) -> LLMResult:
        return LLMResult(text=self.transcript)

    async def usage(self, start, end) -> dict[str, Any]:
        self.usage_calls.append((start, end))
        if isinstance(self.usage_reply, Exception):
            raise self.usage_reply
        return self.usage_reply

    async def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "claude-sub/claude-sonnet-5-5", "billing_type": "subscription",
                 "available": True}, {"id": "gemini/flash", "available": True}]


@dataclass
class FakePort:
    sent: list[tuple[str, str]] = field(default_factory=list)
    ok: bool = True
    inbound: list[InboundMessage] = field(default_factory=list)
    kind: ChannelKind = ChannelKind.TELEGRAM
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA})
    media: bytes = b"audio-bytes"
    media_error: Exception | None = None
    downloads: list[str] = field(default_factory=list)
    voices: list[tuple[str, bytes, float | None]] = field(default_factory=list)
    actions: list[tuple[str, str]] = field(default_factory=list)      # "печатает" / "записывает"
    reads: list[tuple[str, str]] = field(default_factory=list)        # "прочитано" up to an id

    async def send_chat_action(self, external_thread_id: str, action: str) -> None:
        self.actions.append((external_thread_id, action))

    async def mark_read(self, external_thread_id: str, external_message_id: str) -> None:
        """Used only when a test grants Capability.MARK_READ."""
        self.reads.append((external_thread_id, external_message_id))

    async def download_media(self, ref: str) -> bytes:
        """Like the real adapters: a marker (tg-file:/wa-msg:), never a fetchable URL."""
        self.downloads.append(ref)
        if self.media_error is not None:
            raise self.media_error
        return self.media

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        if not self.ok:
            return SendResult(ok=False, error="boom")
        self.sent.append((external_thread_id, text))
        return SendResult(ok=True, external_message_id=f"out-{len(self.sent)}")

    async def send_voice(
        self, external_thread_id: str, audio: bytes, duration_s: float | None
    ) -> SendResult:
        """Used only when a test grants Capability.SEND_VOICE."""
        if not self.ok:
            return SendResult(ok=False, error="boom")
        self.voices.append((external_thread_id, audio, duration_s))
        return SendResult(ok=True, external_message_id=f"voice-{len(self.voices)}")

    async def session_status(self) -> SessionStatus:
        return SessionStatus.ACTIVE

    async def fetch_inbound(self) -> list[InboundMessage]:
        out, self.inbound = self.inbound, []
        return out


class FakeConnector:
    kind = ChannelKind.TELEGRAM
    label = "Fake"
    polling = False

    def __init__(self, kind: ChannelKind = ChannelKind.TELEGRAM, polling: bool = False) -> None:
        self.kind = kind
        self.polling = polling
        self.port = FakePort(kind=kind)

    async def connect(self, channel_id: int, credentials: Mapping[str, str], session: dict) -> ConnectResult:
        return ConnectResult(
            SessionStatus.ACTIVE, account_label="@fakebot",
            session={"token": credentials.get("bot_token", ""), "secret": "hook-secret"},
        )

    async def verify(self, channel_id: int, code: str, session: dict) -> ConnectResult:
        return ConnectResult(SessionStatus.ACTIVE)

    async def state(self, channel_id: int, session: dict) -> ConnectResult:
        return ConnectResult(SessionStatus.ACTIVE if session else SessionStatus.NEW)

    async def disconnect(self, channel_id: int, session: dict) -> None:
        return None

    def build_port(self, channel_id: int, session: dict) -> ChannelPort:
        return self.port

    def verify_webhook(self, headers: Mapping[str, str], session: dict) -> bool:
        return headers.get("x-secret") == session.get("secret")

    def parse_webhook(self, payload: Mapping[str, Any]) -> list[InboundMessage]:
        return [
            InboundMessage(
                external_thread_id=str(payload["chat"]), external_user_id=str(payload["user"]),
                text=payload.get("text", ""), occurred_at=datetime(2026, 11, 5, 12, 0),
                external_id=str(payload["id"]), user_name=payload.get("name"),
            )
        ]


def make_settings(tmp_path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        secret_key=Fernet.generate_key().decode(),
        cookie_secure=False,
        partner_webhook_secret=PARTNER_SECRET,
        tg_login_bot_token=BOT_TOKEN,
        tg_login_bot_username=BOT_USERNAME,
        media_dir=str(tmp_path / "media"),
        static_dir=str(tmp_path / "no-static"),
        reply_debounce_s=5,
        typo_percent=0,        # exact texts in tests; the touches have their own tests
        lowercase_percent=0,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def connector() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
async def container(tmp_path, fake_llm, connector) -> AsyncIterator[Container]:
    c = build_container(make_settings(tmp_path), registry={ChannelKind.TELEGRAM: connector})
    c.llm = fake_llm
    c.speech = None   # never the real Broker; voice tests inject a fake SpeechPort
    c.video = None    # same for AI video notes (VideoPort)

    await create_all(c.engine)
    yield c
    await c.engine.dispose()


@pytest.fixture
async def db(container: Container) -> AsyncIterator[AsyncSession]:
    async with transaction(container.factory) as session:
        yield session


@dataclass
class World:
    strategy: Strategy
    persona: Persona
    channel: Channel


async def make_world(c: Container, *, persona_name: str = "Марина", model: str = "") -> World:
    async with transaction(c.factory) as s:
        strategy = Strategy(
            name="S", goal="Привести к регистрации", offer="Курс RCIPM", partner_name="RCIPM",
            registration_url="https://rcipm.com/?ref={ref}",
            stage_playbook={"engaged": "узнай о человеке", "first_contact": "поздоровайся"},
            rules=["Не дави"], followup_hours=[24, 72, 168],
        )
        s.add(strategy)
        await s.flush()
        persona = Persona(
            name=persona_name, age=45,
            description="Характер: тёплая. Биография: 20 лет в проектах. Стиль общения: коротко.",
            prohibitions="не обещать доход\nне просить пароли", language="ru", strategy_id=strategy.id, model=model,
        )
        s.add(persona)
        await s.flush()
        channel = Channel(
            kind=ChannelKind.TELEGRAM, persona_id=persona.id, name="tg",
            status=SessionStatus.ACTIVE,
            session_enc=c.box.encrypt_json({"secret": "hook-secret", "token": "t"}),
        )
        s.add(channel)
        await s.flush()
        return World(strategy, persona, channel)


@pytest.fixture
async def world(container: Container) -> World:
    return await make_world(container)


def inbound(text: str = "Здравствуйте", ext: str | None = "m1", user: str = "u1", **kw: Any) -> InboundMessage:
    return InboundMessage(
        external_thread_id=kw.pop("thread", "chat-1"), external_user_id=user, text=text,
        occurred_at=kw.pop("at", datetime(2026, 11, 5, 12, 0)), external_id=ext,
        user_name=kw.pop("name", "Анна"), **kw,
    )


@pytest.fixture
async def api(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container=container)
    app.state.container = container  # ASGITransport does not run lifespan
    async with transaction(container.factory) as s:
        for (tg_id,), role in ((OWNER, Role.OWNER), (OPERATOR, Role.OPERATOR),
                               (SUPERADMIN, Role.SUPERADMIN)):
            s.add(AdminUser(telegram_id=tg_id, role=role, display_name=f"admin {tg_id}"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def login(client: httpx.AsyncClient, tg_id: int) -> str:
    """Log in through the Telegram widget endpoint, return the CSRF token."""
    resp = await client.post("/api/auth/telegram", json=widget_payload(tg_id))
    assert resp.status_code == 200, resp.text
    return resp.json()["csrf_token"]


__all__ = ["FakeConnector", "FakeLLM", "LLMError", "World", "decision_json", "guard_json", "login"]
