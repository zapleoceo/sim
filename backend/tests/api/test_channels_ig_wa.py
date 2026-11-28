"""«Подключить» Instagram and WhatsApp through the API (fake instagrapi, fake Evolution): one
channel per kind per persona, the login steps, credentials stored encrypted and never returned,
cancel of a WhatsApp pairing."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.crypto import decrypt_json
from sim.db.models import Channel, Persona
from sim.domain.enums import ChannelKind, ChannelStatus
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp
from tests.fakes.channels import (
    FakeEvolution,
    FakeIgClient,
    FakeIgFactory,
    code_is,
    two_factor,
)

PROXY = "socks5://u:p@10.0.0.1:1080"
IG = {"username": "olya.ig", "password": "secret-pass", "proxy": PROXY}
JID = "79001234567@s.whatsapp.net"


async def new_persona(db: AsyncSession, name: str = "Оля") -> Persona:
    persona = Persona(name=name, language="ru", timezone="Europe/Moscow")
    db.add(persona)
    await db.commit()
    return persona


async def post(api: ApiClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await api.post(path, json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_instagram_password_login(api: ApiClient, world: World, db: AsyncSession,
                                        deps: FakeDeps, ig_factory: FakeIgFactory) -> None:
    persona = await new_persona(db)
    body = await post(api, f"/api/personas/{persona.id}/channels/instagram/login", IG)
    assert body["login"]["state"] == "connected"
    channel = body["channel"]
    assert (channel["kind"], channel["status"], channel["account_label"]) == (
        "instagram", "connected", "@olya.ig")
    row = await db.get(Channel, channel["id"])
    assert row is not None and row.account_id == "4242" and row.credentials_enc
    stored = decrypt_json(row.credentials_enc, deps.settings.secret_key)
    assert stored["proxy"] == PROXY and stored["settings"]["authorization_data"]
    assert (stored["lang"], stored["tz_offset_s"]) == ("ru", 3 * 3600)    # her device
    assert "secret-pass" not in str(stored) and "secret-pass" not in str(body)
    # Telegram and Instagram live side by side: one channel per kind.
    again = await post(api, f"/api/personas/{persona.id}/channels/instagram/login", IG)
    assert again["channel"]["id"] == channel["id"]
    kinds = await db.scalars(select(Channel.kind).where(Channel.persona_id == persona.id))
    assert list(kinds) == [ChannelKind.INSTAGRAM]
    assert ig_factory.last.proxy == PROXY


async def test_instagram_2fa_code_step(api: ApiClient, world: World, db: AsyncSession,
                                       ig_factory: FakeIgFactory) -> None:
    def prepare(client: FakeIgClient) -> None:
        client.logins = [two_factor("totp"), code_is("123456")]

    ig_factory.prepare = prepare
    persona = await new_persona(db)
    asked = await post(api, f"/api/personas/{persona.id}/channels/instagram/login", IG)
    assert (asked["login"]["state"], asked["login"]["detail"]) == ("code", "totp")
    assert asked["channel"]["status"] == "pending"
    channel_id = asked["channel"]["id"]
    done = await post(api, f"/api/personas/{persona.id}/channels/{channel_id}/login/code",
                      {"code": "123 456"})
    assert done["login"]["state"] == "connected" and done["channel"]["status"] == "connected"


async def test_instagram_form_errors(api: ApiClient, world: World, db: AsyncSession) -> None:
    persona = await new_persona(db)
    path = f"/api/personas/{persona.id}/channels/instagram/login"
    unknown = await api.post(path, json={**IG, "cookie": "x"})
    assert unknown.status_code == 400


async def test_instagram_login_without_a_proxy_goes_from_the_server(
        api: ApiClient, world: World, db: AsyncSession, deps: FakeDeps,
        ig_factory: FakeIgFactory) -> None:
    persona = await new_persona(db)
    body = await post(api, f"/api/personas/{persona.id}/channels/instagram/login",
                      {**IG, "proxy": ""})
    assert body["login"]["state"] == "connected"
    assert body["channel"]["status"] == "connected" and ig_factory.last.proxy == ""
    row = await db.get(Channel, body["channel"]["id"])
    assert row is not None and row.credentials_enc
    assert decrypt_json(row.credentials_enc, deps.settings.secret_key)["proxy"] == ""


async def test_one_instagram_account_for_one_persona(api: ApiClient, world: World,
                                                     db: AsyncSession) -> None:
    first = await new_persona(db, "Оля")
    await post(api, f"/api/personas/{first.id}/channels/instagram/login", IG)
    second = await new_persona(db, "Катя")
    body = await post(api, f"/api/personas/{second.id}/channels/instagram/login", IG)
    assert body["login"]["state"] == "error" and "Оля" in body["login"]["message"]


async def test_whatsapp_qr_pairing_and_cancel(api: ApiClient, world: World, db: AsyncSession,
                                              deps: FakeDeps, evolution: FakeEvolution) -> None:
    state = {"value": "connecting"}
    evolution.answers.update({
        ("POST", "/instance/create"): {"qrcode": {"base64": "QQ=="}},
        ("GET", "/instance/connectionState/"): lambda _: {"instance": {"state": state["value"]}},
        ("GET", "/instance/connect/"): {"base64": "Qg=="},
        ("GET", "/instance/fetchInstances"): [{"instance": {"ownerJid": JID}}],
    })
    persona = await new_persona(db)
    started = await post(api, f"/api/personas/{persona.id}/channels/whatsapp/login",
                         {"phone": ""})
    channel = started["channel"]
    assert (started["login"]["state"], channel["status"]) == ("qr", "pending")
    assert started["login"]["qr"] == "data:image/png;base64,QQ=="
    row = await db.get(Channel, channel["id"])
    assert row is not None and row.credentials_enc
    secret = decrypt_json(row.credentials_enc, deps.settings.secret_key)["webhook_secret"]
    assert secret and secret not in str(started)
    base = f"/api/personas/{persona.id}/channels/{channel['id']}/login"
    assert (await api.get(base)).json()["login"]["state"] == "qr"
    state["value"] = "open"
    done = (await api.get(base)).json()
    assert done["login"]["state"] == "connected"
    assert (done["channel"]["status"], done["channel"]["account_label"]) == (
        "connected", "+79001234567")

    # A new pairing that the owner cancels: the instance is unlinked and deleted.
    other = await new_persona(db, "Катя")
    state["value"] = "connecting"
    pending = await post(api, f"/api/personas/{other.id}/channels/whatsapp/login",
                         {"phone": "+7 900"})
    path = f"/api/personas/{other.id}/channels/{pending['channel']['id']}/login"
    assert (await api.delete(path)).status_code == 204
    assert f"/instance/delete/sim-{pending['channel']['id']}" in evolution.paths("DELETE")
    cancelled = await db.get(Channel, pending["channel"]["id"])
    assert cancelled is not None
    await db.refresh(cancelled)
    assert cancelled.status == ChannelStatus.NEW and cancelled.credentials_enc is None
    assert cancelled.account_label == "+7 900"


async def test_chat_rows_name_their_messenger(api: ApiClient, world: World) -> None:
    [row] = (await api.get("/api/chats")).json()["items"]
    assert row["channel"] == {"id": world.channel_id, "kind": "telegram_user",
                              "title": "Марина"}
    assert (await api.get(pp(world, "/channels"))).status_code == 200
