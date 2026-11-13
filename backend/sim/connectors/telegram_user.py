"""Telegram userbot connector: a personal account over MTProto (Telethon), polled by the worker.

Login is a two-step (three with cloud password) dialog, and the connector is stateless between
requests: the unfinished login is parked in the session dict under "pending" (the service
encrypts it) with the unauthorized StringSession and phone_code_hash, so it survives a restart.
The login code and the 2FA password are never stored: they live only in the verify() call.
The live login client is also cached in-process for the same-connection retry.

Never log api_hash, string_session, codes or passwords."""
from __future__ import annotations

import asyncio
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

# Where Telegram delivered the login code (`auth.sentCode.type`). For third-party API apps the
# code usually goes to the Telegram app on the account's other devices, not by SMS: saying "app or
# SMS" sent owners to wait for an SMS that never comes.
CODE_WHERE: dict[str, str] = {
    "app": "Код отправлен в приложение Telegram на ваших других устройствах: откройте чат «Telegram» "
           "(служебные уведомления, синяя галочка). По SMS он не придёт.",
    "sms": "Код отправлен по SMS на номер аккаунта.",
    "call": "Telegram позвонит на номер аккаунта и продиктует код.",
    "flash_call": "Telegram сделает короткий звонок: код это последние цифры номера, с которого позвонили.",
    "email": "Код отправлен на почту, привязанную к аккаунту Telegram для входа.",
    "email_setup": "Telegram требует сначала привязать почту для входа. Войдите в официальном приложении "
                   "Telegram, привяжите почту (Настройки → Конфиденциальность → Почта для входа) и "
                   "подключите аккаунт заново.",
    "fragment": "Код придёт в Fragment (fragment.com): номер аккаунта анонимный.",
}
CODE_WHERE_UNKNOWN = "Введите код входа, который прислал Telegram (проверьте чат «Telegram» в приложении и SMS)."
CODE_TYPES: dict[str, str] = {
    "SentCodeTypeApp": "app", "SentCodeTypeSms": "sms", "SentCodeTypeFirebaseSms": "sms",
    "SentCodeTypeSmsWord": "sms", "SentCodeTypeSmsPhrase": "sms", "SentCodeTypeCall": "call",
    "SentCodeTypeFlashCall": "flash_call", "SentCodeTypeMissedCall": "flash_call",
    "SentCodeTypeEmailCode": "email", "SentCodeTypeSetUpEmailRequired": "email_setup",
    "SentCodeTypeFragmentSms": "fragment",
}


METHOD_QR = "qr"
QR_HINT = ("Откройте Telegram на телефоне: Настройки → Устройства → Подключить устройство, "
           "и отсканируйте этот QR-код. Код обновляется сам каждые ~30 секунд.")


def qr_image(url: str) -> str:
    """`tg://login?token=…` → an SVG data URI the admin shows as an image."""
    import segno  # noqa: PLC0415 — only needed for QR logins

    return segno.make(url, error="m").svg_data_uri(scale=6, border=2)


class QrLogin:
    """A Telethon QR login: the client, its QRLogin and the task waiting for the scan."""

    def __init__(self, client: Any, qr: Any) -> None:
        self.client = client
        self.qr = qr
        self.task: asyncio.Task[Any] = asyncio.get_running_loop().create_task(qr.wait())

    def restart(self) -> None:
        """After `qr.recreate()`: wait for the new token."""
        self.task = asyncio.get_running_loop().create_task(self.qr.wait())


def code_delivery(sent: Any) -> str:
    """`app` / `sms` / `call` / … from Telethon's SentCode; `unknown` when Telegram did not say."""
    return CODE_TYPES.get(type(getattr(sent, "type", None)).__name__, "unknown")


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
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VOICE})

    def __init__(self, client_factory: ClientFactory = build_tg_client) -> None:
        self._factory = client_factory
        self._live: dict[int, Any] = {}           # in-flight login clients
        self._qr: dict[int, QrLogin] = {}         # QR logins waiting for a scan (API process)
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
        if str(credentials.get("method") or "") == METHOD_QR:
            try:
                parse_proxy(proxy)
            except ValueError:
                return ConnectResult(SessionStatus.ERROR, session=base,
                                     message="Неверный прокси. Формат: socks5://user:pass@host:port")
            return await self.connect_qr(channel_id, api_id, api_hash, proxy, base)
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
        delivery = code_delivery(sent)
        logger.info("telegram_user code sent channel=%d via %s", channel_id, delivery)  # no secrets
        pending = {
            "step": NEED_CODE, "api_id": api_id, "api_hash": api_hash, "phone": phone,
            "phone_code_hash": sent.phone_code_hash, "proxy": proxy,
            "string_session": client.session.save(), "code_delivery": delivery,
        }
        return ConnectResult(
            SessionStatus.CHALLENGE, need=NEED_CODE, session={**base, "pending": pending},
            message=CODE_WHERE.get(delivery, CODE_WHERE_UNKNOWN))

    # --- login by QR (no SMS/app code: the owner scans the QR in Telegram → Devices) ---------

    async def connect_qr(
        self, channel_id: int, api_id: int, api_hash: str, proxy: str, base: Session
    ) -> ConnectResult:
        client = self._factory("", api_id, api_hash, proxy)
        try:
            await client.connect()
            qr = await client.qr_login()
        except Exception as exc:
            await self.close_client(client)
            return self.failed(channel_id, base, exc)
        self._qr[channel_id] = QrLogin(client, qr)
        logger.info("telegram_user qr login started channel=%d", channel_id)
        pending = {"step": METHOD_QR, "api_id": api_id, "api_hash": api_hash, "proxy": proxy,
                   "string_session": client.session.save()}
        return ConnectResult(SessionStatus.PENDING, qr_data_uri=qr_image(qr.url),
                             session={**base, "pending": pending}, message=QR_HINT)

    async def qr_state(self, channel_id: int, session: Session) -> ConnectResult:
        """Polled by the admin: still waiting (a fresh QR when the old one expired), signed in,
        or the account asks for its 2FA password."""
        pending = dict(session.get("pending") or {})
        login = self._qr.get(channel_id)
        if login is None:     # the API restarted: the in-memory login is gone
            return ConnectResult(SessionStatus.ERROR, session=without_pending(session),
                                 message="QR-код устарел. Нажмите «Подключить» ещё раз")
        if not login.task.done():
            return ConnectResult(SessionStatus.PENDING, qr_data_uri=qr_image(login.qr.url),
                                 session=dict(session), message=QR_HINT)
        exc = login.task.exception()
        if isinstance(exc, TimeoutError):          # not scanned in time: a new token
            try:
                await login.qr.recreate()
            except Exception as err:
                self.forget_qr(channel_id)
                await self.close_client(login.client)
                return self.failed(channel_id, session, err)
            login.restart()
            return ConnectResult(SessionStatus.PENDING, qr_data_uri=qr_image(login.qr.url),
                                 session=dict(session), message=QR_HINT)
        self.forget_qr(channel_id)
        if isinstance(exc, tg_errors.SessionPasswordNeededError):
            self._live[channel_id] = login.client
            parked = {**pending, "step": NEED_2FA, "string_session": login.client.session.save()}
            return ConnectResult(SessionStatus.CHALLENGE, need=NEED_2FA,
                                 session={**session, "pending": parked},
                                 message="Введите пароль двухфакторной защиты Telegram")
        if exc is not None:
            await self.close_client(login.client)
            return self.failed(channel_id, session, exc)
        return await self.signed_in(channel_id, login.client, pending)

    def forget_qr(self, channel_id: int) -> None:
        login = self._qr.pop(channel_id, None)
        if login is not None and not login.task.done():
            login.task.cancel()

    async def signed_in(self, channel_id: int, client: Any, pending: Mapping[str, Any]) -> ConnectResult:
        """The account is authorized on `client`: store its session and close the login client."""
        me = await client.get_me()
        string_session = client.session.save()
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
            return await self.signed_in(channel_id, client, pending)
        except tg_errors.SessionPasswordNeededError:
            assert client is not None
            parked = {**pending, "step": NEED_2FA, "string_session": client.session.save()}
            return ConnectResult(SessionStatus.CHALLENGE, need=NEED_2FA,
                                 session={**session, "pending": parked},
                                 message="Введите пароль двухфакторной защиты Telegram")
        except Exception as exc:
            return self.failed(channel_id, session, exc, pending=pending, step=step)

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
        login = self._qr.get(channel_id)
        self.forget_qr(channel_id)
        if login is not None:
            await self.close_client(login.client)
        live = self._live.pop(channel_id, None)
        if live is not None:
            await self.close_client(live)
        rt = self._runtime.pop(channel_id, None)
        if rt is not None:
            await rt.transport.close(logout=logout)

    # --- state / lifecycle -----------------------------------------------------------------

    async def state(self, channel_id: int, session: Session) -> ConnectResult:
        pending = session.get("pending")
        if isinstance(pending, dict) and pending.get("step") == METHOD_QR:
            return await self.qr_state(channel_id, session)
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

