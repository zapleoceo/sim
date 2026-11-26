"""`/api/personas/{id}/stickers` (sim/api/routes/stickers.py, docs/stickers.md): what her
Telegram accounts have, and «Обновить» — a re-read of every Telegram account of hers."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Persona, PersonaSticker
from sim.domain.enums import ChannelKind, ChannelStatus
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient, World, pp


def sticker(persona_id: int, channel_id: int, doc_id: int, **fields: object) -> PersonaSticker:
    return PersonaSticker(persona_id=persona_id, channel_id=channel_id, doc_id=doc_id, set_id=10,
                          set_access_hash=11, **fields)


async def other_persona(db: AsyncSession) -> tuple[Persona, Channel]:
    persona = Persona(name="Оля")
    db.add(persona)
    await db.flush()
    channel = Channel(persona_id=persona.id, kind=ChannelKind.TELEGRAM_USER, title="Оля",
                      status=ChannelStatus.CONNECTED)
    db.add(channel)
    await db.flush()
    return persona, channel


async def test_her_stickers_are_listed_favourites_first(api: ApiClient, world: World,
                                                        db: AsyncSession) -> None:
    olya, olya_channel = await other_persona(db)
    late = sticker(world.persona_id, world.channel_id, 1, emoji="😂", set_name="ducks",
                   set_title="Утята", position=1001)
    fav = sticker(world.persona_id, world.channel_id, 2, emoji="🥰", faved=True)
    first = sticker(world.persona_id, world.channel_id, 3, emoji="🦆", set_name="ducks",
                    set_title="Утята", position=1000)
    db.add_all([late, fav, first, sticker(olya.id, olya_channel.id, 1, emoji="🙈")])
    await db.commit()

    response = await api.get(pp(world, "/stickers"))

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["emoji"] for item in items] == ["🥰", "🦆", "😂"]      # not Olya's
    assert items[2] == {"id": late.id, "channel_id": world.channel_id, "emoji": "😂",
                        "set_name": "ducks", "set_title": "Утята", "faved": False}
    assert items[0]["faved"] is True and items[0]["set_title"] == ""


async def test_a_persona_without_stickers_has_an_empty_list(api: ApiClient,
                                                            world: World) -> None:
    response = await api.get(pp(world, "/stickers"))
    assert response.status_code == 200 and response.json() == {"items": []}


async def test_refresh_queues_a_read_of_each_telegram_account(api: ApiClient, world: World,
                                                              db: AsyncSession,
                                                              deps: FakeDeps) -> None:
    second = Channel(persona_id=world.persona_id, kind=ChannelKind.TELEGRAM_USER,
                     title="Марина 2", status=ChannelStatus.CONNECTED)
    instagram = Channel(persona_id=world.persona_id, kind=ChannelKind.INSTAGRAM,
                        title="Марина IG", status=ChannelStatus.CONNECTED)
    db.add_all([second, instagram])
    await other_persona(db)
    await db.commit()

    response = await api.post(pp(world, "/stickers/sync"))

    assert response.status_code == 202
    accounts = sorted([world.channel_id, second.id])
    assert sorted(response.json()["channels"]) == accounts       # no Instagram, not Olya's
    queued = [args for name, args, _ in deps.tasks.calls if name == "sync_stickers"]
    assert sorted(channel_id for (channel_id,) in queued) == accounts


async def test_an_unknown_persona_is_404(api: ApiClient, deps: FakeDeps) -> None:
    assert (await api.get("/api/personas/999999/stickers")).status_code == 404
    assert (await api.post("/api/personas/999999/stickers/sync")).status_code == 404
    assert deps.tasks.calls == []


async def test_stickers_need_a_login(client: ApiClient, world: World) -> None:
    assert (await client.get(pp(world, "/stickers"))).status_code == 401
