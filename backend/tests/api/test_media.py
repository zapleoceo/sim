"""Media library: upload, list, edit, delete, authenticated file streaming with Range."""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sim.api.files import RangeNotSatisfiable, parse_range
from sim.db.models import Generation, MediaAsset, Message, Persona
from sim.domain.enums import (
    Author,
    Direction,
    GenerationKind,
    GenerationStatus,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
)
from tests.api.fakes import PNG, FakeDeps
from tests.api.helpers import ApiClient, World, pp


async def upload(api: ApiClient, world: World, **form: str) -> dict[str, Any]:
    response = await api.post(pp(world, "/media"),
                              files={"file": ("me.png", PNG, "image/png")},
                              data={"caption": "у окна", "tags": "селфи, Утро,,",
                                    "is_reference": "true", **form})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def add_incoming(db: AsyncSession, deps: FakeDeps, world: World) -> int:
    """A file the client sent into the world's chat (incoming media have no persona)."""
    key = await deps.storage.put(b"%PDF-1.4", mime="application/pdf", prefix="incoming")
    media = MediaAsset(source=MediaSource.INCOMING, kind=MediaKind.FILE, storage_key=key,
                       mime="application/pdf", file_name="договор.pdf", size_bytes=8,
                       sha256="0" * 64)
    db.add(media)
    await db.flush()
    db.add(Message(chat_id=world.chat_id, direction=Direction.IN, author=Author.CLIENT,
                   kind=MessageKind.FILE, text="", media_id=media.id,
                   status=MessageStatus.RECEIVED))
    await db.commit()
    return media.id


def test_parse_range() -> None:
    assert parse_range(None, 10) is None
    assert parse_range("bytes=0-3", 10) == (0, 3)
    assert parse_range("bytes=5-", 10) == (5, 9)
    assert parse_range("bytes=-4", 10) == (6, 9)
    assert parse_range("bytes=8-100", 10) == (8, 9)
    assert parse_range("bytes=0-1,4-5", 10) is None
    assert parse_range("items=0-1", 10) is None
    with pytest.raises(RangeNotSatisfiable):
        parse_range("bytes=10-12", 10)


async def test_upload_to_library(api: ApiClient, world: World, deps: FakeDeps) -> None:
    media = await upload(api, world)
    assert (media["kind"], media["source"], media["mime"]) == ("photo", "library", "image/png")
    assert media["is_reference"] is True
    assert media["caption"] == "у окна"
    assert media["tags"] == ["селфи", "утро"]
    assert media["url"] == f"/api/media/{media['id']}/file"
    assert PNG in deps.storage.files.values()


async def test_upload_rejects_non_media_and_missing_file(api: ApiClient, world: World) -> None:
    pdf = await api.post(pp(world, "/media"),
                         files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert pdf.status_code == 400
    assert "error" in pdf.json()
    empty = await api.post(pp(world, "/media"), data={"caption": "x"},
                           files={"other": ("a.png", PNG, "image/png")})
    assert empty.status_code == 400


async def test_list_media_filters(api: ApiClient, world: World, db: AsyncSession,
                                  deps: FakeDeps) -> None:
    media = await upload(api, world)
    incoming_id = await add_incoming(db, deps, world)
    default = [m["id"] for m in (await api.get(pp(world, "/media"))).json()["items"]]
    assert default == [media["id"]]
    library = await api.get(pp(world, "/media"), params={"source": "library", "kind": "photo"})
    assert [m["id"] for m in library.json()["items"]] == [media["id"]]
    videos = await api.get(pp(world, "/media"), params={"kind": "video"})
    assert videos.json()["items"] == []
    incoming = await api.get(pp(world, "/media"), params={"source": "incoming"})
    assert [m["id"] for m in incoming.json()["items"]] == [incoming_id]


async def test_each_persona_lists_only_her_media(api: ApiClient, world: World,
                                                 db: AsyncSession, deps: FakeDeps) -> None:
    media = await upload(api, world)
    await add_incoming(db, deps, world)
    other = Persona(name="Оля")
    db.add(other)
    await db.commit()
    mine = await api.get(f"/api/personas/{other.id}/media")
    assert mine.status_code == 200 and mine.json()["items"] == []
    theirs = await api.get(f"/api/personas/{other.id}/media", params={"source": "incoming"})
    assert theirs.json()["items"] == []
    assert media["id"] not in [m["id"] for m in mine.json()["items"]]
    assert (await api.get("/api/personas/999999/media")).status_code == 404


async def test_edit_media(api: ApiClient, world: World) -> None:
    media = await upload(api, world)
    response = await api.patch(f"/api/media/{media['id']}",
                               json={"caption": "новая", "tags": ["Дом"], "is_reference": False})
    assert response.status_code == 200
    body = response.json()
    assert (body["caption"], body["tags"], body["is_reference"]) == ("новая", ["дом"], False)
    only_caption = await api.patch(f"/api/media/{media['id']}", json={"caption": "ещё"})
    assert only_caption.json()["tags"] == ["дом"]
    assert (await api.patch("/api/media/999999", json={"caption": "x"})).status_code == 404


async def test_delete_media(api: ApiClient, world: World, deps: FakeDeps) -> None:
    media = await upload(api, world)
    assert (await api.delete(f"/api/media/{media['id']}")).status_code == 204
    assert deps.storage.files == {}
    assert (await api.get(f"/api/media/{media['id']}/file")).status_code == 404
    assert (await api.delete(f"/api/media/{media['id']}")).status_code == 404


async def test_file_streaming_with_range(api: ApiClient, world: World) -> None:
    media = await upload(api, world)
    url = media["url"]
    whole = await api.get(url)
    assert whole.status_code == 200
    assert whole.content == PNG
    assert whole.headers["content-type"].startswith("image/png")
    assert whole.headers["x-content-type-options"] == "nosniff"
    assert whole.headers["content-disposition"] == "inline"

    part = await api.get(url, headers={"Range": "bytes=0-7"})
    assert part.status_code == 206
    assert part.content == PNG[:8]
    assert part.headers["content-range"] == f"bytes 0-7/{len(PNG)}"

    beyond = await api.get(url, headers={"Range": f"bytes={len(PNG)}-"})
    assert beyond.status_code == 416


async def test_non_media_file_is_a_download(api: ApiClient, world: World, db: AsyncSession,
                                            deps: FakeDeps) -> None:
    media_id = await add_incoming(db, deps, world)
    response = await api.get(f"/api/media/{media_id}/file")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment;")


async def test_file_needs_login(client: ApiClient, world: World, db: AsyncSession,
                                deps: FakeDeps) -> None:
    media_id = await add_incoming(db, deps, world)
    assert (await client.get(f"/api/media/{media_id}/file")).status_code == 401


async def generated_room_photo(db: AsyncSession, world: World, room: str = "кухня") -> int:
    """A generated photo whose generation was made in a room of her apartment."""
    generation = Generation(persona_id=world.persona_id, kind=GenerationKind.PHOTO, prompt="p",
                            scene={"room": room, "place": "дом, кухня", "outfit": "свитер",
                                   "activity": "готовит", "light": "evening_lamp"},
                            status=GenerationStatus.DONE)
    db.add(generation)
    await db.flush()
    photo = MediaAsset(persona_id=world.persona_id, source=MediaSource.GENERATED,
                       kind=MediaKind.PHOTO, storage_key=f"generated/{generation.id}.png",
                       mime="image/png", sha256="0" * 64, generation_id=generation.id)
    db.add(photo)
    await db.commit()
    return photo.id


async def test_the_library_shows_the_scene_and_the_date(api: ApiClient, world: World,
                                                        db: AsyncSession) -> None:
    photo_id = await generated_room_photo(db, world)
    uploaded = await upload(api, world)
    items = {m["id"]: m for m in (await api.get(pp(world, "/media"))).json()["items"]}
    assert items[photo_id]["scene"]["room"] == "кухня"
    assert items[photo_id]["scene"]["activity"] == "готовит"
    assert items[photo_id]["created_at"]
    assert items[uploaded["id"]]["scene"] is None and uploaded["scene"] is None


async def test_a_generated_photo_is_pinned_as_its_room_reference(
        api: ApiClient, world: World, db: AsyncSession) -> None:
    photo_id = await generated_room_photo(db, world)
    pinned = await api.patch(f"/api/media/{photo_id}", json={"is_room_ref": True})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["scene"]["room_ref"] is True
    assert pinned.json()["scene"]["room"] == "кухня"
    unpinned = await api.patch(f"/api/media/{photo_id}", json={"is_room_ref": False})
    assert "room_ref" not in unpinned.json()["scene"]
    # Her face can be a generated photo too: the owner's pick of her best face.
    face = await api.patch(f"/api/media/{photo_id}", json={"is_reference": True})
    assert face.json()["is_reference"] is True


async def test_only_a_photo_of_her_room_can_be_a_room_reference(
        api: ApiClient, world: World, db: AsyncSession) -> None:
    library = await upload(api, world)
    refused = await api.patch(f"/api/media/{library['id']}", json={"is_room_ref": True})
    assert refused.status_code == 400
    assert "Эталоном комнаты может быть только" in refused.json()["error"]
    video = MediaAsset(persona_id=world.persona_id, source=MediaSource.LIBRARY,
                       kind=MediaKind.VIDEO, storage_key="library/v.mp4", mime="video/mp4",
                       sha256="0" * 64)
    db.add(video)
    await db.commit()
    as_face = await api.patch(f"/api/media/{video.id}", json={"is_reference": True})
    assert as_face.status_code == 400
    assert "только фото" in as_face.json()["error"]
