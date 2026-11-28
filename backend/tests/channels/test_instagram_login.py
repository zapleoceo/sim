"""Connecting an Instagram account (sim/channels/instagram/login.py) with a fake instagrapi
client: password, 2FA codes, «approve in the app» retried by itself, a challenge code handed to
instagrapi's handler, a manual check, sessionid, a pasted session JSON, errors."""
from __future__ import annotations

import asyncio
import json

from sim.channels.instagram.login import (
    CODE_WRONG,
    IgLoginRequest,
    InstagramLogins,
)
from sim.channels.login import LoginState
from tests.fakes.channels import (
    OWN_ID,
    BadPassword,
    ChallengeRequired,
    FakeIgClient,
    FakeIgFactory,
    challenge,
    code_is,
    raising,
    two_factor,
)

PROXY = "socks5://u:p@10.0.0.1:1080"
PASSWORD = IgLoginRequest(proxy=PROXY, username="olya.ig", password="secret", lang="ru",
                          tz_offset_s=10800)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def logins(*steps: object, clock: Clock | None = None,
           default_proxy: str = "") -> tuple[InstagramLogins, FakeIgFactory]:
    def prepare(client: FakeIgClient) -> None:
        client.logins = list(steps)  # type: ignore[arg-type]

    factory = FakeIgFactory(prepare)
    return InstagramLogins(factory, default_proxy=default_proxy, attempt_s=5,
                           monotonic=clock or Clock()), factory


async def test_password_login_connects_and_keeps_only_the_session() -> None:
    flow, factory = logins()
    result = await flow.start(1, PASSWORD)
    assert result.state == LoginState.CONNECTED
    assert (result.account_id, result.account_label) == (OWN_ID, "@olya.ig")
    assert result.credentials is not None
    assert set(result.credentials) == {"settings", "proxy", "username", "lang", "tz_offset_s"}
    assert result.credentials["proxy"] == PROXY and "secret" not in json.dumps(
        result.credentials)
    assert result.view() == {"state": "connected", "message": result.message, "qr": None,
                             "detail": None}
    # The device geo follows the persona and the proxy.
    assert (factory.last.proxy, factory.last.lang, factory.last.tz_offset_s) == (
        PROXY, "ru", 10800)
    assert (await flow.poll(1)).state == LoginState.NONE        # the login is over


async def test_without_a_proxy_she_logs_in_from_the_server_or_through_its_default() -> None:
    flow, factory = logins()
    result = await flow.start(1, IgLoginRequest(proxy="", username="a", password="b",
                                                lang="ru"))
    assert result.state == LoginState.CONNECTED
    assert factory.last.proxy == ""                     # the server's own IP
    assert result.credentials is not None and result.credentials["proxy"] == ""
    flow, factory = logins(default_proxy=PROXY)
    result = await flow.start(1, IgLoginRequest(proxy="", username="a", password="b"))
    assert result.state == LoginState.CONNECTED and factory.last.proxy == PROXY


async def test_a_totp_code_and_a_wrong_one() -> None:
    flow, factory = logins(two_factor("totp"), code_is("123456"), code_is("123456"))
    asked = await flow.start(1, PASSWORD)
    assert (asked.state, asked.detail) == (LoginState.CODE, "totp")
    wrong = await flow.answer(1, "000 000")
    assert (wrong.state, wrong.message) == (LoginState.CODE, CODE_WRONG)
    done = await flow.answer(1, "123 456")
    assert done.state == LoginState.CONNECTED
    assert factory.last.login_calls[-1] == ("olya.ig", "secret", "123456")
    assert len(factory.made) == 1                       # the same client (device) all along


async def test_an_sms_code_is_named_as_such() -> None:
    flow, _ = logins(two_factor("sms"))
    asked = await flow.start(1, PASSWORD)
    assert (asked.state, asked.detail) == (LoginState.CODE, "sms")
    assert "SMS" in asked.message


async def test_approval_in_the_app_is_retried_after_the_pause() -> None:
    clock = Clock()
    flow, factory = logins(two_factor("device"), two_factor("device"), clock=clock)
    first = await flow.start(1, PASSWORD)
    assert first.state == LoginState.DEVICE and "приложении Instagram" in first.message
    assert (await flow.poll(1)).state == LoginState.DEVICE     # too early: no new login
    assert len(factory.last.login_calls) == 1
    clock.now += 8
    again = await flow.poll(1)                                 # retried, still waiting
    assert again.state == LoginState.DEVICE and len(factory.last.login_calls) == 2
    clock.now += 14
    assert (await flow.poll(1)).state == LoginState.DEVICE     # the next pause is 15 s
    clock.now += 1
    assert (await flow.poll(1)).state == LoginState.CONNECTED
    assert len(factory.last.login_calls) == 3


async def test_a_challenge_code_goes_to_instagrapis_handler() -> None:
    flow, factory = logins(challenge("654321"))
    asked = await flow.start(1, PASSWORD)
    assert (asked.state, asked.detail) == (LoginState.CHALLENGE, "email")
    assert "на почту" in asked.message
    done = await flow.answer(1, "654 321")
    assert done.state == LoginState.CONNECTED
    assert len(factory.last.login_calls) == 1                  # one login, the code inside it


async def test_a_wrong_challenge_code_asks_again() -> None:
    flow, _ = logins(challenge("654321"), challenge("111111"))
    await flow.start(1, PASSWORD)
    wrong = await flow.answer(1, "000000")
    assert (wrong.state, wrong.message) == (LoginState.CHALLENGE, CODE_WRONG)
    again = await flow.answer(1, "999999")      # that attempt is over: log in again, new code
    assert again.state == LoginState.CHALLENGE and again.message != CODE_WRONG
    assert (await flow.answer(1, "111111")).state == LoginState.CONNECTED


async def test_a_manual_check_then_retry() -> None:
    flow, _ = logins(raising(ChallengeRequired("Manual verification required")))
    asked = await flow.start(1, PASSWORD)
    assert asked.state == LoginState.MANUAL and "Повторить" in asked.message
    assert (await flow.answer(1, "")).state == LoginState.CONNECTED


async def test_a_wrong_password_ends_the_login() -> None:
    flow, _ = logins(raising(BadPassword("The password you entered is incorrect")))
    result = await flow.start(1, PASSWORD)
    assert (result.state, result.message) == (LoginState.ERROR, "Неверный логин или пароль")
    assert (await flow.poll(1)).state == LoginState.NONE


async def test_sessionid_login() -> None:
    flow, _ = logins()
    result = await flow.start(1, IgLoginRequest(proxy=PROXY, sessionid="777%3Aabc%3A1"))
    assert (result.state, result.account_id) == (LoginState.CONNECTED, "777")
    bad = await flow.start(1, IgLoginRequest(proxy=PROXY, sessionid="nope"))
    assert bad.state == LoginState.ERROR


async def test_a_pasted_session_json() -> None:
    flow, factory = logins()
    session = {"authorization_data": {"ds_user_id": "555", "sessionid": "555%3Ax"},
               "uuids": {"phone_id": "p"}}
    result = await flow.start(1, IgLoginRequest(proxy=PROXY, settings_json=json.dumps(session)))
    assert (result.state, result.account_id, result.account_label) == (
        LoginState.CONNECTED, "555", "@olya.json")
    assert factory.last.settings["uuids"] == {"phone_id": "p"}     # the device is restored
    assert factory.last.login_calls == []                          # never logged in again
    not_json = await flow.start(1, IgLoginRequest(proxy=PROXY, settings_json="{oops"))
    assert not_json.state == LoginState.ERROR
    no_session = await flow.start(1, IgLoginRequest(proxy=PROXY, settings_json="{}"))
    assert no_session.state == LoginState.ERROR and "authorization_data" in no_session.message


async def test_cancel_frees_a_waiting_challenge_handler() -> None:
    flow, _ = logins(challenge("654321"))
    await flow.start(1, PASSWORD)
    pending = flow.pending[1]
    await flow.cancel(1)
    assert pending.task is not None
    await asyncio.wait_for(asyncio.gather(pending.task, return_exceptions=True), 5)
    assert (await flow.poll(1)).state == LoginState.NONE
