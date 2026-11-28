"""Connecting an Instagram account from the dashboard (docs/channels.md, «Вход в Instagram»).
Runs in the `api` process; instagrapi's blocking calls run in threads.

Three ways in:
- login + password -> signed in | a 2FA code (TOTP / SMS) | «approve in the app» on another
  device (the same client logs in again by itself after 8, 15, 25, 40, 60 s) | a code Instagram
  sent by e-mail / SMS (a challenge) | a manual check in the app, then «Повторить»;
- the browser's `sessionid` cookie (`login_by_sessionid`): the only way in for many accounts
  with 2FA, where the private-API login stops at Instagram's Bloks screens;
- a pasted instagrapi settings JSON (a session made elsewhere).

The proxy is optional (the owner, 2026-11-28: «сделай прокси опциональный, и попробуем без
прокси» — the server is in Germany, where she lives). Without one (and without
SIM_IG_DEFAULT_PROXY) the login and all later work go from the server's own IP, with no device
geo set; a proxy, once used, stays the account's for good.

A challenge asks for its code from INSIDE instagrapi (`challenge_code_handler`), so the login
runs in a thread whose handler waits for the code the owner types (`CodeBridge`); a 2FA code
logs in again on the same client with `verification_code`. Every step answers within
`ATTEMPT_S`: signed in, a question for the owner, or an error.

One pending login per channel, in this process's memory, dropped after `LOGIN_TTL_S`. The
password lives only there; on success the route stores {settings, proxy, username, lang,
tz_offset_s} encrypted (the settings dump keeps the device ids and cookies — the worker restores
it and never logs in by itself). Never logged: passwords, codes, cookies, the settings dump."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sim.channels.instagram.client import (
    ClientFactory,
    CodeRequired,
    build_ig_client,
    is_manual,
    own_user_id,
)
from sim.channels.login import LoginResult, LoginState

log = logging.getLogger(__name__)

LOGIN_TTL_S = 600.0
ATTEMPT_S = 90.0
CODE_WAIT_S = 540.0                         # the challenge handler waits this long for a code
DEVICE_BACKOFF_S = (8.0, 15.0, 25.0, 40.0, 60.0)
MAX_CODE_TRIES = 5

EXPIRED = "Вход не начат или устарел. Нажмите «Подключить» ещё раз."
TEXTS = {
    "totp": "Введите код из приложения-аутентификатора (Google Authenticator и т. п.).",
    "sms": "Введите код из SMS, которое прислал Instagram.",
    "device": ("Подтвердите вход в приложении Instagram на телефоне («Это я»). SIM повторит "
               "вход сам через несколько секунд."),
    "challenge": "Instagram прислал код подтверждения ({where}). Введите его.",
    "manual": ("Instagram просит подтвердить вход в его приложении. Откройте Instagram на "
               "телефоне, подтвердите, затем нажмите «Повторить»."),
    "connected": "Аккаунт подключён",
}
ERRORS = {
    "BadPassword": "Неверный логин или пароль",
    "BadCredentials": "Неверный логин или пароль",
    "InvalidUser": "Такого аккаунта нет",
    "UserNotFound": "Такого аккаунта нет",
    "PleaseWaitFewMinutes": "Instagram просит подождать несколько минут",
    "RateLimitError": "Instagram просит подождать несколько минут",
    "ProxyAddressIsBlocked": "Instagram заблокировал этот прокси — нужен другой",
    "ClientConnectionError": "Не удалось подключиться к Instagram (сеть или прокси)",
    "ClientRequestTimeout": "Instagram не ответил вовремя (сеть или прокси)",
    "CodeRequired": "Код не введён вовремя. Начните вход заново",
    "PasswordKeyMissing": ("Instagram не дал ключ для пароля. Попробуйте ещё раз через минуту "
                           "или войдите по sessionid"),
    "SessionInvalid": "Сессия недействительна: войдите в Instagram в браузере заново",
    "BadSession": "Сессия недействительна: войдите в Instagram в браузере заново",
}
CODE_WRONG = "Код не подошёл, проверьте и введите ещё раз"
# Instagram screens no code from SIM can pass: the owner checks in the app, then «Повторить».
MANUAL_FORMS = frozenset({"SelectContactPointRecoveryForm", "RecaptchaChallengeForm",
                          "SubmitPhoneNumberForm"})


@dataclass(frozen=True)
class IgLoginRequest:
    """What the owner typed (sim/api/schemas.InstagramLoginIn). Exactly one way in."""

    proxy: str
    username: str = ""
    password: str = field(default="", repr=False)
    sessionid: str = field(default="", repr=False)
    settings_json: str = field(default="", repr=False)
    lang: str = ""
    tz_offset_s: int | None = None


class CodeBridge:
    """Hands the code the owner types to instagrapi's challenge handler, which runs in the
    login thread and blocks until the code comes (or `CODE_WAIT_S` passes)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, wait_s: float = CODE_WAIT_S) -> None:
        self.loop = loop
        self.wait_s = wait_s
        self.codes: queue.Queue[str | None] = queue.Queue()
        self.asked: asyncio.Future[str] | None = None

    def arm(self) -> asyncio.Future[str]:
        self.asked = self.loop.create_future()
        return self.asked

    def handler(self, _username: Any = None, choice: Any = None) -> str:
        """instagrapi's `challenge_code_handler`, called in the login thread."""
        where = "email" if "mail" in str(choice).lower() else "sms"
        self.loop.call_soon_threadsafe(self._ask, where)
        try:
            code = self.codes.get(timeout=self.wait_s)
        except queue.Empty:
            raise CodeRequired("no code in time") from None
        if code is None:
            raise CodeRequired("login cancelled")
        return code

    def _ask(self, where: str) -> None:
        if self.asked is not None and not self.asked.done():
            self.asked.set_result(where)

    def give(self, code: str | None) -> None:
        self.codes.put(code)


@dataclass
class _Pending:
    client: Any
    request: IgLoginRequest
    bridge: CodeBridge
    started: float
    first: Callable[[], Any]                # the way in; repeated after «approve» / «Повторить»
    state: LoginState = LoginState.NONE
    detail: str | None = None
    task: asyncio.Task[Any] | None = None
    retry_at: float = 0.0
    retries: int = 0
    code_tries: int = 0


def two_factor_kind(client: Any) -> str:
    """totp | sms when a code is to be typed; device = approve a push on another device."""
    last = getattr(client, "last_json", None)
    info = (last.get("two_factor_info") or {}) if isinstance(last, dict) else {}
    if info.get("totp_two_factor_on"):
        return "totp"
    if info.get("sms_two_factor_on"):
        return "sms"
    return "device"


def error_text(exc: BaseException) -> str:
    name = type(exc).__name__
    return ERRORS.get(name, f"Не удалось войти ({name})")


class InstagramLogins:
    """Pending Instagram logins of this process, by channel id."""

    def __init__(self, factory: ClientFactory = build_ig_client, *,
                 default_proxy: str = "", ttl_s: float = LOGIN_TTL_S,
                 attempt_s: float = ATTEMPT_S,
                 monotonic: Callable[[], float] = time.monotonic,
                 backoff_s: tuple[float, ...] = DEVICE_BACKOFF_S) -> None:
        self.factory = factory
        self.default_proxy = default_proxy
        self.ttl_s = ttl_s
        self.attempt_s = attempt_s
        self.monotonic = monotonic
        self.backoff_s = backoff_s
        self.pending: dict[int, _Pending] = {}

    # --- the owner's steps ---------------------------------------------------------------

    async def start(self, channel_id: int, request: IgLoginRequest) -> LoginResult:
        await self.cancel(channel_id)
        await self.sweep()
        proxy = request.proxy.strip() or self.default_proxy
        if not proxy:                       # the owner's choice: straight from the server's IP
            log.info("instagram login channel=%s: no proxy, from the server's own IP",
                     channel_id)
        request = replace(request, proxy=proxy)
        settings: dict[str, Any] | None = None
        if request.settings_json.strip():
            try:
                settings = json.loads(request.settings_json)
            except ValueError:
                return LoginResult(LoginState.ERROR, "Это не JSON настроек instagrapi")
            if not isinstance(settings, dict) or not settings.get("authorization_data"):
                return LoginResult(LoginState.ERROR,
                                   "В JSON нет authorization_data — это не сессия instagrapi")
        loop = asyncio.get_running_loop()
        bridge = CodeBridge(loop)
        try:
            client = self.factory(settings, proxy=proxy, lang=request.lang,
                                  tz_offset_s=request.tz_offset_s, code_handler=bridge.handler)
        except Exception as exc:
            log.warning("instagram login channel=%s: no client (%s)", channel_id,
                        type(exc).__name__)
            return LoginResult(LoginState.ERROR, error_text(exc))
        sessionid = request.sessionid.strip()
        if settings is not None:
            first: Callable[[], Any] = client.account_info
        elif sessionid:
            first = lambda: client.login_by_sessionid(sessionid)  # noqa: E731
        elif request.username.strip() and request.password:
            username, password = request.username.strip(), request.password
            first = lambda: client.login(username, password)  # noqa: E731
        else:
            return LoginResult(LoginState.ERROR, "Нужны логин и пароль (или sessionid)")
        self.pending[channel_id] = _Pending(client=client, request=request, bridge=bridge,
                                            started=self.monotonic(), first=first)
        return await self._attempt(channel_id, first)

    async def poll(self, channel_id: int) -> LoginResult:
        """Re-read by the dashboard: «approve in the app» is retried here when its pause ends."""
        await self.sweep()
        login = self.pending.get(channel_id)
        if login is None:
            return LoginResult(LoginState.NONE, EXPIRED)
        if login.state == LoginState.DEVICE and self.monotonic() >= login.retry_at:
            return await self._attempt(channel_id, login.first)
        return self._question(login)

    async def answer(self, channel_id: int, code: str) -> LoginResult:
        """A 2FA / challenge code; empty = «Повторить» after a manual check or a device
        approval."""
        await self.sweep()
        login = self.pending.get(channel_id)
        if login is None:
            return LoginResult(LoginState.NONE, EXPIRED)
        code = "".join(code.split())
        if login.state == LoginState.CHALLENGE and code:
            login.code_tries += 1
            if login.task is not None and not login.task.done():
                login.bridge.give(code)                 # the handler is waiting for it
                return await self._wait(channel_id, login)
            # The last code was refused and that attempt is over: log in again, Instagram
            # sends a new code and asks for it.
            return await self._attempt(channel_id, login.first)
        if login.state == LoginState.CODE and code:
            login.code_tries += 1
            request = login.request
            return await self._attempt(channel_id, lambda: login.client.login(
                request.username.strip(), request.password, verification_code=code))
        if login.state in (LoginState.DEVICE, LoginState.MANUAL):
            return await self._attempt(channel_id, login.first)
        return self._question(login)

    async def cancel(self, channel_id: int) -> None:
        login = self.pending.pop(channel_id, None)
        if login is None:
            return
        login.bridge.give(None)             # a waiting challenge handler gives up
        if login.task is not None and not login.task.done():
            login.task.cancel()
            with contextlib.suppress(BaseException):
                await login.task

    async def sweep(self) -> None:
        now = self.monotonic()
        for channel_id in [cid for cid, login in self.pending.items()
                           if now - login.started > self.ttl_s]:
            log.info("instagram login channel=%s expired", channel_id)
            await self.cancel(channel_id)

    async def close_all(self, _app: object = None) -> None:
        for channel_id in list(self.pending):
            await self.cancel(channel_id)

    # --- the attempts --------------------------------------------------------------------

    async def _attempt(self, channel_id: int, call: Callable[[], Any]) -> LoginResult:
        login = self.pending[channel_id]
        login.task = asyncio.create_task(asyncio.to_thread(call))
        return await self._wait(channel_id, login)

    async def _wait(self, channel_id: int, login: _Pending) -> LoginResult:
        """Until the running attempt ends or Instagram asks for a challenge code."""
        task = login.task
        if task is None:
            return self._question(login)
        asked = login.bridge.arm()
        done, _ = await asyncio.wait({task, asked}, timeout=self.attempt_s,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            asked.cancel()
            return await self._outcome(channel_id, login, task)
        if asked in done:
            login.state, login.detail = LoginState.CHALLENGE, asked.result()
            log.info("instagram login channel=%s: challenge code asked", channel_id)
            return self._question(login)
        asked.cancel()
        log.warning("instagram login channel=%s: no answer from Instagram", channel_id)
        await self.cancel(channel_id)
        return LoginResult(LoginState.ERROR, ERRORS["ClientRequestTimeout"])

    async def _outcome(self, channel_id: int, login: _Pending,
                       task: asyncio.Task[Any]) -> LoginResult:
        exc = task.exception() if not task.cancelled() else CodeRequired("cancelled")
        if exc is None:
            return await self._finish(channel_id, login)
        name = type(exc).__name__
        if name == "TwoFactorRequired":
            if is_manual(exc):
                return self._park(channel_id, login, LoginState.MANUAL, None)
            kind = two_factor_kind(login.client)
            if kind == "device":
                return self._park(channel_id, login, LoginState.DEVICE, None)
            if login.state == LoginState.CODE and login.code_tries:
                return self._retry_code(channel_id, login)
            return self._park(channel_id, login, LoginState.CODE, kind)
        if login.state == LoginState.CHALLENGE and login.code_tries and name != "CodeRequired":
            return self._retry_code(channel_id, login)      # the typed code was refused
        if name.startswith("Challenge") or name in MANUAL_FORMS or is_manual(exc):
            return self._park(channel_id, login, LoginState.MANUAL, None)
        if login.state == LoginState.CODE and name not in ("BadPassword", "BadCredentials"):
            return self._retry_code(channel_id, login)
        log.warning("instagram login channel=%s failed: %s", channel_id, name)
        await self.cancel(channel_id)
        return LoginResult(LoginState.ERROR, error_text(exc))

    def _retry_code(self, channel_id: int, login: _Pending) -> LoginResult:
        if login.code_tries >= MAX_CODE_TRIES:
            self.pending.pop(channel_id, None)
            return LoginResult(LoginState.ERROR, "Слишком много неверных кодов. Начните заново")
        log.info("instagram login channel=%s: code refused", channel_id)
        return LoginResult(login.state, CODE_WRONG, detail=login.detail)

    def _park(self, channel_id: int, login: _Pending, state: LoginState,
              detail: str | None) -> LoginResult:
        if state == LoginState.DEVICE:
            pause = self.backoff_s[min(login.retries, len(self.backoff_s) - 1)]
            login.retry_at = self.monotonic() + pause
            login.retries += 1
        login.state, login.detail = state, detail
        log.info("instagram login channel=%s: %s", channel_id, state.value)
        return self._question(login)

    def _question(self, login: _Pending) -> LoginResult:
        state = login.state
        if state == LoginState.CODE:
            return LoginResult(state, TEXTS[login.detail or "sms"], detail=login.detail)
        if state == LoginState.CHALLENGE:
            where = "на почту" if login.detail == "email" else "в SMS"
            return LoginResult(state, TEXTS["challenge"].format(where=where),
                               detail=login.detail)
        if state in (LoginState.DEVICE, LoginState.MANUAL):
            return LoginResult(state, TEXTS[state.value])
        return LoginResult(LoginState.NONE, EXPIRED)

    async def _finish(self, channel_id: int, login: _Pending) -> LoginResult:
        """Signed in: the settings dump, our id and @username; the login is dropped."""
        client = login.client
        try:
            settings = client.get_settings()
            username = str(getattr(client, "username", "") or login.request.username.strip())
            if not username:
                info = await asyncio.to_thread(client.account_info)
                username = str(getattr(info, "username", "") or "")
        except Exception as exc:
            log.warning("instagram login channel=%s: no session (%s)", channel_id,
                        type(exc).__name__)
            await self.cancel(channel_id)
            return LoginResult(LoginState.ERROR, error_text(exc))
        account_id = own_user_id(client) or None
        self.pending.pop(channel_id, None)
        if account_id is None:
            return LoginResult(LoginState.ERROR, "Instagram не сообщил id аккаунта: войдите "
                                                 "заново")
        request = login.request
        log.info("instagram login channel=%s: signed in", channel_id)
        username = username.lstrip("@")
        return LoginResult(
            LoginState.CONNECTED, TEXTS["connected"], account_id=account_id,
            account_label=f"@{username}" if username else account_id,
            credentials={"settings": settings, "proxy": request.proxy, "username": username,
                         "lang": request.lang, "tz_offset_s": request.tz_offset_s})
