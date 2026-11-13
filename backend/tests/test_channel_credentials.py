"""Channel credentials are saved encrypted, so a reconnect needs no typing."""
from __future__ import annotations

from sim.adapters.db.models import Channel
from sim.services.channels import SAVED, merge_credentials, saved_credentials
from tests.test_api_smoke import owner


def test_saved_credentials_fall_back_to_a_parked_telegram_login():
    assert saved_credentials(None) == {}
    assert saved_credentials({SAVED: {"bot_token": "1:a", "proxy": ""}}) == {"bot_token": "1:a"}
    parked = {"pending": {"api_id": 123, "api_hash": "h", "phone": "+1", "phone_code_hash": "x"}}
    assert saved_credentials(parked) == {"api_id": "123", "api_hash": "h", "phone": "+1"}


def test_typed_fields_win_and_empty_ones_reuse_the_saved():
    assert merge_credentials({"api_id": "1", "phone": "+1"}, {"api_id": " ", "phone": "+2"}) == \
        {"api_id": "1", "phone": "+2"}


async def test_reconnect_with_empty_fields_reuses_the_encrypted_credentials(api, world, container):
    h = await owner(api)
    ch = (await api.post("/api/channels", json={"kind": "telegram", "persona_id": world.persona.id,
                                                "name": "bot"}, headers=h)).json()
    assert ch["has_saved_credentials"] is False
    await api.post(f"/api/channels/{ch['id']}/connect", json={"bot_token": "123:abc"}, headers=h)
    async with container.factory() as s:
        row = await s.get(Channel, ch["id"])
        assert "123:abc" not in row.session_enc                                   # encrypted at rest
        assert container.box.decrypt_json(row.session_enc)[SAVED] == {"bot_token": "123:abc"}
    listed = {c["id"]: c for c in (await api.get("/api/channels")).json()["items"]}
    assert listed[ch["id"]]["has_saved_credentials"] is True

    again = await api.post(f"/api/channels/{ch['id']}/connect", json={"bot_token": ""}, headers=h)
    assert again.json() == {"status": "active"}
    async with container.factory() as s:
        row = await s.get(Channel, ch["id"])
        assert container.box.decrypt_json(row.session_enc)["token"] == "123:abc"   # reused


async def test_owner_reads_the_saved_credentials_to_prefill_the_form(api, world):
    from tests.conftest import OPERATOR, login

    h = await owner(api)
    ch = (await api.post("/api/channels", json={"kind": "telegram", "persona_id": world.persona.id,
                                                "name": "bot"}, headers=h)).json()
    assert (await api.get(f"/api/channels/{ch['id']}/credentials")).json() == {"fields": {}, "saved_secrets": []}
    await api.post(f"/api/channels/{ch['id']}/connect", json={"bot_token": "123:abc"}, headers=h)
    got = await api.get(f"/api/channels/{ch['id']}/credentials")
    assert got.json() == {"fields": {}, "saved_secrets": ["bot_token"]}       # the token never comes back
    assert got.headers["cache-control"] == "no-store"
    await login(api, OPERATOR[0])
    assert (await api.get(f"/api/channels/{ch['id']}/credentials")).status_code == 403


def test_public_view_shows_ids_and_phone_but_only_names_of_secrets():
    from sim.services.channels import public_view

    saved = {"api_id": "1", "api_hash": "h", "phone": "+1", "proxy": "socks5://u:p@h:1", "password": "pw",
             "username": "me"}
    assert public_view(saved) == {"fields": {"api_id": "1", "phone": "+1", "username": "me"},
                                  "saved_secrets": ["api_hash", "password", "proxy"]}


async def test_an_undecryptable_session_does_not_break_the_channel_list(api, world, container):
    async with container.factory() as s:
        row = await s.get(Channel, world.channel.id)
        row.session_enc = "not-a-fernet-token"
        await s.commit()
    await owner(api)
    listed = (await api.get("/api/channels")).json()["items"]
    assert any(c["id"] == world.channel.id and c["has_saved_credentials"] is False for c in listed)
