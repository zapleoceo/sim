"""«Канал» on the persona page: list, QR login of a Telegram account (fake Telethon), 2FA,
an expired QR, storing the encrypted session, one account per persona, «Отключить»."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.crypto import decrypt_json
from sim.db.models import Channel, Persona
from sim.domain.enums import ChannelStatus
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp
from tests.fakes.telegram import LoginClientFactory

LOGIN = {"api_id": 12345, "api_hash": "0123456789abcdef", "proxy": ""}


async def settle() -> None:
    """Let the task waiting for the scan see what the fake QR did."""
    for _ in range(3):
        await asyncio.sleep(0)


async def new_persona(db: AsyncSession, name: str = "Оля") -> Persona:
    persona = Persona(name=name)
    db.add(persona)
    await db.commit()
    return persona


async def start(api: ApiClient, persona_id: int) -> dict[str, Any]:
    response = await api.post(f"/api/personas/{persona_id}/channels/telegram/login", json=LOGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_channels_of_a_persona(api: ApiClient, world: World) -> None:
    response = await api.get(pp(world, "/channels"))
    [channel] = response.json()["items"]
    assert channel["id"] == world.channel_id and channel["persona_id"] == world.persona_id
    assert (channel["kind"], channel["status"], channel["account_label"]) == (
        "telegram_user", "connected", "@marina")
    assert "credentials_enc" not in channel
    # What «Подключить» offers here (the tests give the app a fake Evolution).
    kinds = [(k["kind"], k["label"], k["available"]) for k in response.json()["kinds"]]
    assert kinds == [("telegram_user", "Telegram", True), ("instagram", "Instagram", True),
                     ("whatsapp", "WhatsApp", True)]
    assert (await api.get("/api/personas/999999/channels")).status_code == 404


async def test_qr_login_connects_the_account(api: ApiClient, world: World, db: AsyncSession,
                                             deps: FakeDeps,
                                             login_factory: LoginClientFactory) -> None:
    persona = await new_persona(db)
    started = await start(api, persona.id)
    channel = started["channel"]
    # A login in progress: `pending` until it ends (docs/channels.md).
    assert channel["status"] == "pending" and channel["persona_id"] == persona.id
    assert started["login"] == {"state": "qr", "qr": "svg:tg://login?token=T1",
                                "message": started["login"]["message"], "detail": None}
    assert login_factory.args == [(12345, "0123456789abcdef", "")]
    poll = f"/api/personas/{persona.id}/channels/{channel['id']}/login"
    assert (await api.get(poll)).json()["login"]["state"] == "qr"

    login_factory.qr.scan()
    await settle()
    done = await api.get(poll)
    assert done.json()["login"]["state"] == "connected"
    assert done.json()["channel"]["status"] == "connected"
    assert done.json()["channel"]["account_label"] == "@olya"
    assert "0123456789abcdef" not in done.text and "SESSION-STRING" not in done.text
    row = await db.get(Channel, channel["id"])
    assert row is not None and row.account_id == "777" and row.credentials_enc
    assert decrypt_json(row.credentials_enc, deps.settings.secret_key) == {
        "api_id": 12345, "api_hash": "0123456789abcdef", "session": "SESSION-STRING",
        "proxy": ""}
    assert not login_factory.last.connected                  # the login client is closed
    assert (await api.get(poll)).json()["login"]["state"] == "none"


async def test_two_factor_password_step(api: ApiClient, world: World, db: AsyncSession,
                                        login_factory: LoginClientFactory) -> None:
    persona = await new_persona(db)
    channel_id = (await start(api, persona.id))["channel"]["id"]
    base = f"/api/personas/{persona.id}/channels/{channel_id}/login"
    login_factory.qr.need_password()
    await settle()
    assert (await api.get(base)).json()["login"]["state"] == "password"

    wrong = await api.post(f"{base}/password", json={"password": "nope"})
    assert wrong.json()["login"]["state"] == "password"
    assert "Неверный пароль" in wrong.json()["login"]["message"]
    right = await api.post(f"{base}/password", json={"password": "secret-2fa"})
    assert right.json()["login"]["state"] == "connected"
    assert "secret-2fa" not in right.text
    assert login_factory.last.passwords == ["nope", "secret-2fa"]


async def test_an_expired_qr_is_renewed_and_errors_end_the_login(
        api: ApiClient, world: World, db: AsyncSession,
        login_factory: LoginClientFactory) -> None:
    persona = await new_persona(db)
    channel_id = (await start(api, persona.id))["channel"]["id"]
    base = f"/api/personas/{persona.id}/channels/{channel_id}/login"
    login_factory.qr.expire()
    await settle()
    renewed = (await api.get(base)).json()["login"]
    assert renewed == {**renewed, "state": "qr", "qr": "svg:tg://login?token=T2"}

    class ApiIdInvalidError(Exception):
        pass

    login_factory.qr.fail(ApiIdInvalidError("bad"))
    await settle()
    failed = (await api.get(base)).json()
    assert failed["login"]["state"] == "error"
    assert "api_id" in failed["login"]["message"]
    assert failed["channel"]["status"] == "new"


async def test_cancel_and_bad_proxy(api: ApiClient, world: World, db: AsyncSession,
                                    login_factory: LoginClientFactory) -> None:
    persona = await new_persona(db)
    channel_id = (await start(api, persona.id))["channel"]["id"]
    base = f"/api/personas/{persona.id}/channels/{channel_id}/login"
    assert (await api.delete(base)).status_code == 204
    assert not login_factory.last.connected
    assert (await api.get(base)).json()["login"]["state"] == "none"
    bad = await api.post(f"/api/personas/{persona.id}/channels/telegram/login",
                         json={**LOGIN, "proxy": "ftp://host:1"})
    assert bad.json()["login"]["state"] == "error" and "прокси" in bad.json()["login"]["message"]
    # Another persona's channel is not reachable through this persona.
    other = f"/api/personas/{world.persona_id}/channels/{channel_id}/login"
    assert (await api.get(other)).status_code == 404


async def test_one_account_works_for_one_persona(api: ApiClient, world: World,
                                                 db: AsyncSession,
                                                 login_factory: LoginClientFactory) -> None:
    marina = await db.get(Channel, world.channel_id)
    assert marina is not None
    marina.account_id = "777"
    await db.commit()
    persona = await new_persona(db)
    channel_id = (await start(api, persona.id))["channel"]["id"]
    login_factory.qr.scan()
    await settle()
    body = (await api.get(f"/api/personas/{persona.id}/channels/{channel_id}/login")).json()
    assert body["login"]["state"] == "error" and "Марина" in body["login"]["message"]
    assert body["channel"]["status"] == "new"


async def test_relogin_reuses_the_channel_and_disable(api: ApiClient, world: World,
                                                      db: AsyncSession) -> None:
    started = await start(api, world.persona_id)
    assert started["channel"]["id"] == world.channel_id    # her one Telegram channel
    disabled = await api.post(pp(world, f"/channels/{world.channel_id}/disable"))
    assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"
    row = await db.get(Channel, world.channel_id)
    assert row is not None and row.status == ChannelStatus.DISABLED
