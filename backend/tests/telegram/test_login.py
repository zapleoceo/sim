"""TelegramLogins (QR login in the api process) without the database: states, the 2FA step,
the expiry of a forgotten login, error texts, and that secrets never reach the view or logs."""
from __future__ import annotations

import asyncio
import logging

import pytest

from sim.telegram.login import LoginState, TelegramLogins, error_message
from tests.fakes.telegram import LoginClientFactory


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


def make(factory: LoginClientFactory, clock: Clock | None = None) -> TelegramLogins:
    return TelegramLogins(factory, ttl_s=60, monotonic=clock or Clock(),
                          qr_image=lambda url: f"svg:{url}")


async def test_scan_then_connected_with_credentials(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    factory = LoginClientFactory()
    logins = make(factory)
    started = await logins.start(5, 111, "hash-secret-123", "")
    assert (started.state, started.qr) == (LoginState.QR, "svg:tg://login?token=T1")
    assert (await logins.poll(5)).state == LoginState.QR
    factory.qr.scan()
    await settle()
    done = await logins.poll(5)
    assert done.state == LoginState.CONNECTED
    assert done.credentials == {"api_id": 111, "api_hash": "hash-secret-123",
                                "session": "SESSION-STRING", "proxy": ""}
    # The shared login step of every channel (sim/channels/login.py): no secrets in the view.
    assert done.view() == {"state": "connected", "message": done.message, "qr": None,
                           "detail": None}
    assert "hash-secret-123" not in repr(done) and "SESSION-STRING" not in repr(done)
    assert "hash-secret-123" not in caplog.text and "SESSION-STRING" not in caplog.text
    assert (await logins.poll(5)).state == LoginState.NONE


async def test_password_step_and_wrong_password() -> None:
    factory = LoginClientFactory(twofa="right")
    logins = make(factory)
    await logins.start(5, 111, "hash-secret-123")
    factory.qr.need_password()
    await settle()
    assert (await logins.poll(5)).state == LoginState.PASSWORD
    wrong = await logins.password(5, "wrong")
    assert wrong.state == LoginState.PASSWORD and "Неверный пароль" in wrong.message
    assert (await logins.password(5, "right")).state == LoginState.CONNECTED


async def test_a_forgotten_login_expires_and_is_closed() -> None:
    factory, clock = LoginClientFactory(), Clock()
    logins = make(factory, clock)
    await logins.start(5, 111, "hash-secret-123")
    assert factory.last.connected
    clock.now += 61
    assert (await logins.poll(5)).state == LoginState.NONE
    assert not factory.last.connected and logins.pending == {}


async def test_a_new_start_replaces_the_old_login_and_close_all() -> None:
    factory = LoginClientFactory()
    logins = make(factory)
    await logins.start(5, 111, "hash-secret-123")
    await logins.start(5, 111, "hash-secret-123")
    assert not factory.clients[0].connected and factory.clients[1].connected
    await logins.close_all()
    assert not factory.clients[1].connected and logins.pending == {}


async def test_a_connect_error_ends_the_login() -> None:
    class FloodWaitError(Exception):
        seconds = 30

    logins = make(LoginClientFactory(connect_error=FloodWaitError()))
    result = await logins.start(5, 111, "hash-secret-123")
    assert result.state == LoginState.ERROR and "30 с" in result.message
    assert logins.pending == {}


def test_error_messages() -> None:
    class ApiIdInvalidError(Exception):
        pass

    assert "api_id" in error_message(ApiIdInvalidError())
    assert error_message(ValueError("x")) == "Не удалось войти (ValueError)"
