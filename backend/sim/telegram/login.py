"""Connecting a Telegram account to a persona from the dashboard: QR login (docs/personas.md,
«Канал»). Runs in the `api` process.

  start(api_id, api_hash, proxy) -> a Telethon client, `qr_login()`, a task waiting for the scan
  poll()     -> still waiting (a fresh QR when the old one expired) | the account asks for its
                2FA password | signed in (the StringSession is handed back once) | error
  password() -> the 2FA step
  cancel()   -> drop the login

The unfinished login lives only in this process's memory, one per channel, and is dropped after
`LOGIN_TTL_S` (or when the process restarts: the owner presses «Подключить» again). Nothing is
written to the database here; the route stores the result encrypted.

Never log api_hash, the session, QR tokens or passwords: only channel ids and error class names."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sim.channels.login import LoginResult, LoginState
from sim.telegram.client import Credentials, build_client, parse_proxy

log = logging.getLogger(__name__)

LOGIN_TTL_S = 600.0
QR_HINT = ("Откройте Telegram на телефоне: Настройки → Устройства → Подключить устройство, и "
           "отсканируйте QR-код. Код обновляется сам примерно раз в 30 секунд.")
TWO_FA_HINT = "Введите пароль двухфакторной защиты Telegram (облачный пароль)."
EXPIRED = "Вход не начат или устарел. Нажмите «Подключить» ещё раз."
ERRORS = {
    "ApiIdInvalidError": "Неверная пара api_id / api_hash (my.telegram.org)",
    "PasswordHashInvalidError": "Неверный пароль двухфакторной защиты",
    "AuthTokenExpiredError": "QR-код устарел. Нажмите «Подключить» ещё раз",
    "PhoneNumberBannedError": "Этот аккаунт заблокирован в Telegram",
}

LoginClientFactory = Callable[[int, str, str], Any]
Monotonic = Callable[[], float]
QrImage = Callable[[str], str]
# The shared login step (sim/channels/login.py); CONNECTED carries `credentials` =
# {api_id, api_hash, session, proxy} and `me`. Re-exported for the API and the tests.
__all__ = ["LoginResult", "LoginState", "TelegramLogins", "error_message", "login_client",
           "qr_data_uri"]


@dataclass
class _Pending:
    client: Any
    qr: Any
    api_id: int
    api_hash: str = field(repr=False)
    proxy: str = field(repr=False)
    started: float
    task: asyncio.Task[Any] | None = None
    needs_password: bool = False

    def wait(self) -> None:
        self.task = asyncio.get_running_loop().create_task(self.qr.wait())


def login_client(api_id: int, api_hash: str, proxy: str) -> Any:
    """A fresh, unauthorized client (empty StringSession) of the shared factory."""
    return build_client(Credentials(api_id, api_hash, "", proxy), receive_updates=False)


def qr_data_uri(url: str) -> str:
    """`tg://login?token=…` -> an SVG data URI the dashboard shows as an image."""
    import segno

    return str(segno.make(url, error="m").svg_data_uri(scale=6, border=2))


def error_message(exc: BaseException) -> str:
    name = type(exc).__name__
    if name == "FloodWaitError":
        return f"Telegram просит подождать {int(getattr(exc, 'seconds', 0) or 0)} с"
    return ERRORS.get(name, f"Не удалось войти ({name})")


class TelegramLogins:
    """Pending QR logins of this process, by channel id."""

    def __init__(self, factory: LoginClientFactory = login_client, *,
                 ttl_s: float = LOGIN_TTL_S, monotonic: Monotonic = time.monotonic,
                 qr_image: QrImage = qr_data_uri) -> None:
        self.factory = factory
        self.ttl_s = ttl_s
        self.monotonic = monotonic
        self.qr_image = qr_image
        self.pending: dict[int, _Pending] = {}

    async def start(self, channel_id: int, api_id: int, api_hash: str,
                    proxy: str = "") -> LoginResult:
        await self.cancel(channel_id)
        await self.sweep()
        try:
            parse_proxy(proxy)
        except ValueError:
            return LoginResult(LoginState.ERROR,
                               "Неверный прокси. Формат: socks5://user:pass@host:port")
        client = self.factory(api_id, api_hash, proxy)
        try:
            await client.connect()
            qr = await client.qr_login()
        except Exception as exc:
            await self.close(client)
            log.warning("telegram login channel=%s failed to start: %s", channel_id,
                        type(exc).__name__)
            return LoginResult(LoginState.ERROR, error_message(exc))
        login = _Pending(client=client, qr=qr, api_id=api_id, api_hash=api_hash, proxy=proxy,
                         started=self.monotonic())
        login.wait()
        self.pending[channel_id] = login
        log.info("telegram login channel=%s: QR shown", channel_id)
        return LoginResult(LoginState.QR, QR_HINT, qr=self.qr_image(qr.url))

    async def poll(self, channel_id: int) -> LoginResult:
        await self.sweep()
        login = self.pending.get(channel_id)
        if login is None:
            return LoginResult(LoginState.NONE, EXPIRED)
        if login.needs_password:
            return LoginResult(LoginState.PASSWORD, TWO_FA_HINT)
        task = login.task
        if task is None or not task.done():
            return LoginResult(LoginState.QR, QR_HINT, qr=self.qr_image(login.qr.url))
        exc = None if task.cancelled() else task.exception()
        if task.cancelled() or isinstance(exc, TimeoutError):
            try:                                        # not scanned in time: a new token
                await login.qr.recreate()
            except Exception as err:
                return await self.fail(channel_id, err)
            login.wait()
            return LoginResult(LoginState.QR, QR_HINT, qr=self.qr_image(login.qr.url))
        if exc is not None and type(exc).__name__ == "SessionPasswordNeededError":
            login.needs_password = True
            log.info("telegram login channel=%s: 2FA password needed", channel_id)
            return LoginResult(LoginState.PASSWORD, TWO_FA_HINT)
        if exc is not None:
            return await self.fail(channel_id, exc)
        return await self.finish(channel_id)

    async def password(self, channel_id: int, password: str) -> LoginResult:
        await self.sweep()
        login = self.pending.get(channel_id)
        if login is None:
            return LoginResult(LoginState.NONE, EXPIRED)
        if not login.needs_password:
            return await self.poll(channel_id)
        try:
            await login.client.sign_in(password=password)
        except Exception as exc:
            if type(exc).__name__ in {"PasswordHashInvalidError", "FloodWaitError"}:
                log.info("telegram login channel=%s: password refused (%s)", channel_id,
                         type(exc).__name__)
                return LoginResult(LoginState.PASSWORD, error_message(exc))
            return await self.fail(channel_id, exc)
        return await self.finish(channel_id)

    async def cancel(self, channel_id: int) -> None:
        login = self.pending.pop(channel_id, None)
        if login is None:
            return
        if login.task is not None and not login.task.done():
            login.task.cancel()
            with contextlib.suppress(BaseException):
                await login.task
        await self.close(login.client)

    async def sweep(self) -> None:
        """Drop logins older than `ttl_s` (the owner walked away)."""
        now = self.monotonic()
        for channel_id in [cid for cid, login in self.pending.items()
                           if now - login.started > self.ttl_s]:
            log.info("telegram login channel=%s expired", channel_id)
            await self.cancel(channel_id)

    async def finish(self, channel_id: int) -> LoginResult:
        """Signed in: read the account and the session, then close the login client."""
        login = self.pending[channel_id]
        try:
            me = await login.client.get_me()
            session = login.client.session.save()
        except Exception as exc:
            return await self.fail(channel_id, exc)
        self.pending.pop(channel_id, None)
        await self.close(login.client)
        log.info("telegram login channel=%s: signed in", channel_id)
        credentials = {"api_id": login.api_id, "api_hash": login.api_hash, "session": session,
                       "proxy": login.proxy}
        return LoginResult(LoginState.CONNECTED, "Аккаунт подключён", credentials=credentials,
                           me=me)

    async def fail(self, channel_id: int, exc: BaseException) -> LoginResult:
        log.warning("telegram login channel=%s failed: %s", channel_id, type(exc).__name__)
        await self.cancel(channel_id)
        return LoginResult(LoginState.ERROR, error_message(exc))

    async def close_all(self, _app: object = None) -> None:
        """Shutdown hook of the API (Litestar passes the app)."""
        for channel_id in list(self.pending):
            await self.cancel(channel_id)

    @staticmethod
    async def close(client: Any) -> None:
        with contextlib.suppress(Exception):
            await client.disconnect()
