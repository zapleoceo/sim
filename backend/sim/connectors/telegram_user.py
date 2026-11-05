"""Telegram userbot connector: a personal account over MTProto (Telethon), polled by the worker.

Login is a two-step (three with cloud password) dialog, and the connector is stateless between
requests: the unfinished login is parked in the session dict under "pending" (the service
encrypts it) with the unauthorized StringSession and phone_code_hash, so it survives a restart.
The login code and the 2FA password are never stored: they live only in the verify() call.
The live login client is also cached in-process for the same-connection retry.

Never log api_hash, string_session, codes or passwords."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

from telethon import errors as tg_errors

from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.telegram_user import (
    TelegramUserAdapter,
    TelethonTransport,
    TGUserState,
)
from sim.adapters.channels.tg_client import build_tg_client, parse_proxy
from sim.config import settings
from sim.connectors.spec import Session
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import ChannelPort, ConnectResult, InboundMessage

logger = logging.getLogger(__name__)

ClientFactory = Callable[..., Any]

NEED_CODE = "code"
NEED_2FA = "2fa"


def label_of(me: Any, phone: str) -> str:
    username = getattr(me, "username", None)
    if username:
        return f"@{username}"
    number = str(getattr(me, "phone", "") or phone).lstrip("+")
    return f"+{number}" if number else str(getattr(me, "first_name", "") or "Telegram")


def without_pending(session: Session) -> Session:
    """Truthy (the service skips empty dicts) and drops a stale login."""
    return {**session, "pending": None}


class Runtime:
    """What the connector keeps per channel between port builds."""

    def __init__(self, fingerprint: str, transport: TelethonTransport) -> None:
        self.fingerprint = fingerprint
        self.transport = transport
        self.state = TGUserState()


class TelegramUserConnector:
    kind = ChannelKind.TELEGRAM_USER
    label = "Telegram (аккаунт)"
    polling = True
    poll_interval_setting = "tg_user_poll_interval_s"
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA})

    def __init__(self, client_factory: ClientFactory = build_tg_client) -> None:
        self._factory = client_factory
        self._live: dict[int, Any] = {}           # in-flight login clients
        self._runtime: dict[int, Runtime] = {}    # connected polling/sending clients

    # --- login -----------------------------------------------------------------------------

    async def connect(
        self, channel_id: int, credentials: Mapping[str, str], session: Session
    ) -> ConnectResult:
        await self.drop(channel_id)
        base = without_pending(session)
        api_hash = str(credentials.get("api_hash", "")).strip()
        phone = re.sub(r"[^\d+]", "", str(credentials.get("phone", "")))
        proxy = str(credentials.get("proxy") or "").strip()
        try:
            api_id = int(str(credentials.get("api_id", "")).strip())
        except ValueError:
            api_id = 0
        if api_id <= 0 or not api_hash:
            return ConnectResult(SessionStatus.ERROR, session=base,
                                 message="Укажите api_id (число) и api_hash с my.telegram.org")
        if not re.fullmatch(r"\+?\d{7,15}", phone):
            return ConnectResult(SessionStatus.ERROR, session=base,
                                 message="Неверный номер телефона. Формат: +380501234567")
        phone = phone if phone.startswith("+") else f"+{phone}"
        try:
            parse_proxy(proxy)
        except ValueError:
            return ConnectResult(SessionStatus.ERROR, session=base,
                                 message="Неверный прокси. Формат: socks5://user:pass@host:port")
        client = self._factory("", api_id, api_hash, proxy)
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
        except Exception as exc:
            await self.close_client(client)
            return self.failed(channel_id, base, exc)
        self._live[channel_id] = client
        pending = {
            "step": NEED_CODE, "api_id": api_id, "api_hash": api_hash, "phone": phone,
            "phone_code_hash": sent.phone_code_hash, "proxy": proxy,
            "string_session": client.session.save(),
        }
        return ConnectResult(
            SessionStatus.CHALLENGE, need=NEED_CODE, session={**base, "pending": pending},
            message="Введите код, который Telegram отправил в приложение или по SMS")

    async def verify(self, channel_id: int, code: str, session: Session) -> ConnectResult:
        pending = session.get("pending")
        if not isinstance(pending, dict):
            return ConnectResult(SessionStatus.ERROR, message="Вход не начат: подключите заново",
                                 session=dict(session))
        step = str(pending.get("step") or NEED_CODE)
        client = self._live.get(channel_id)
        try:
            if client is None:
                client = self._factory(str(pending.get("string_session") or ""),
                                       int(pending["api_id"]), str(pending["api_hash"]),
                                       str(pending.get("proxy") or ""))
                await client.connect()
                self._live[channel_id] = client
            if step == NEED_2FA:
                await client.sign_in(password=code)
            else:
                await client.sign_in(phone=pending["phone"], code=re.sub(r"\D", "", code),
                                     phone_code_hash=pending["phone_code_hash"])
            me = await client.get_me()
            string_session = client.session.save()
        except tg_errors.SessionPasswordNeededError:
            assert client is not None
            parked = {**pending, "step": NEED_2FA, "string_session": client.session.save()}
            return ConnectResult(SessionStatus.CHALLENGE, need=NEED_2FA,
                                 session={**session, "pending": parked},
                                 message="Введите пароль двухфакторной защиты Telegram")
        except Exception as exc:
            return self.failed(channel_id, session, exc, pending=pending, step=step)
        await self.close_client(client)
        self._live.pop(channel_id, None)
        label = label_of(me, str(pending.get("phone") or ""))
        done: dict[str, Any] = {
            "api_id": pending["api_id"], "api_hash": pending["api_hash"],
            "string_session": string_session, "account_label": label,
        }
        if pending.get("proxy"):
            done["proxy"] = pending["proxy"]
        return ConnectResult(SessionStatus.ACTIVE, account_label=label, session=done)

    def failed(
        self, channel_id: int, session: Session, exc: Exception,
        *, pending: dict[str, Any] | None = None, step: str = NEED_CODE,
    ) -> ConnectResult:
        """Map a Telethon error to a Russian message. A wrong code/password keeps the dialog
        open (CHALLENGE) so the owner can retry; everything else ends it (ERROR)."""
        name = type(exc).__name__
        logger.warning("telegram_user login failed channel=%d: %s", channel_id, name)  # no secrets
        retry = {
            tg_errors.PhoneCodeInvalidError: ("Неверный код. Проверьте и введите снова", NEED_CODE),
            tg_errors.PasswordHashInvalidError: ("Неверный пароль двухфакторной защиты", NEED_2FA),
        }
        for cls, (msg, need) in retry.items():
            if isinstance(exc, cls) and pending is not None:
                return ConnectResult(SessionStatus.CHALLENGE, need=need, message=msg,
                                     session={**session, "pending": pending})
        if isinstance(exc, tg_errors.FloodWaitError):
            seconds = int(getattr(exc, "seconds", 0) or 0)
            msg = f"Telegram просит подождать {seconds} с. Повторите позже"
            if pending is not None:       # too many attempts: the login stays valid
                return ConnectResult(SessionStatus.CHALLENGE, need=step, message=msg,
                                     session={**session, "pending": pending})
        else:
            msg = {
                "PhoneNumberInvalidError": "Неверный номер телефона. Формат: +380501234567",
                "PhoneNumberBannedError": "Этот номер заблокирован в Telegram",
                "PhoneCodeExpiredError": "Код истёк. Подключите аккаунт заново",
                "PhoneCodeEmptyError": "Введите код",
                "ApiIdInvalidError": "Неверная пара api_id / api_hash",
                "PhoneNumberFloodError": "Слишком много запросов кода. Повторите позже",
            }.get(name, f"Не удалось войти ({name})")
        self.live_forget(channel_id)
        return ConnectResult(SessionStatus.ERROR, message=msg, session=without_pending(session))

    def live_forget(self, channel_id: int) -> None:
        self._live.pop(channel_id, None)

    @staticmethod
    async def close_client(client: Any) -> None:
        try:
            await client.disconnect()
        except Exception as exc:
            logger.warning("telegram_user disconnect failed: %s", type(exc).__name__)

    async def drop(self, channel_id: int, *, logout: bool = False) -> None:
        live = self._live.pop(channel_id, None)
        if live is not None:
            await self.close_client(live)
        rt = self._runtime.pop(channel_id, None)
        if rt is not None:
            await rt.transport.close(logout=logout)

    # --- state / lifecycle -----------------------------------------------------------------

    async def state(self, channel_id: int, session: Session) -> ConnectResult:
        pending = session.get("pending")
        if isinstance(pending, dict):
            return ConnectResult(SessionStatus.CHALLENGE, session=dict(session),
                                 need=NEED_2FA if pending.get("step") == NEED_2FA else NEED_CODE)
        if not session.get("string_session"):
            return ConnectResult(SessionStatus.NEW, session=dict(session))
        label = str(session.get("account_label") or "") or None
        status = await self.build_port(channel_id, session).session_status()
        message = "Сессия Telegram завершена: подключите аккаунт заново" \
            if status is SessionStatus.EXPIRED else None
        return ConnectResult(status, account_label=label, message=message, session=dict(session))

    async def disconnect(self, channel_id: int, session: Session) -> None:
        # Unlinking ends OUR session on Telegram too (log_out): otherwise a ghost device stays
        # in the account's "Active sessions" with a key nobody holds any more.
        if session.get("string_session"):
            self.build_port(channel_id, session)     # a runtime to log out through
        await self.drop(channel_id, logout=True)

    def build_port(self, channel_id: int, session: Session) -> ChannelPort:
        string_session = str(session.get("string_session") or "")
        if not string_session:
            raise ValueError(f"telegram_user channel {channel_id} has no active session")
        rt = self._runtime.get(channel_id)
        if rt is None or rt.fingerprint != string_session:
            api_id, api_hash = int(session["api_id"]), str(session["api_hash"])
            proxy = str(session.get("proxy") or "")
            rt = Runtime(string_session, TelethonTransport(
                lambda: self._factory(string_session, api_id, api_hash, proxy)))
            self._runtime[channel_id] = rt
        return TelegramUserAdapter(
            rt.transport, rt.state,
            min_send_interval_s=settings().tg_user_min_send_interval_s)

    def verify_webhook(self, headers: Mapping[str, str], session: Session) -> bool:
        return False

    def parse_webhook(self, payload: Mapping[str, Any]) -> list[InboundMessage]:
        return []

