"""Persona media gallery: upload, list, filter, download and delete."""
from __future__ import annotations

import io

from sim.adapters.db.models import PersonaMedia
from sim.adapters.db.repositories.persona_media import PersonaMediaRepo
from sim.adapters.db.session import transaction
from sim.domain.enums import MediaKind
from tests.conftest import OWNER, login

ONE_PX_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' ',#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"
    b"\x22q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16"
    b"\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz"
    b"\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99"
    b"\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7"
    b"\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5"
    b"\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1"
    b"\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd2\x8a+\xff\xd9"
)


async def owner(api) -> dict[str, str]:
    return {"X-CSRF-Token": await login(api, *OWNER)}


async def test_upload_list_and_delete(api, world):
    h = await owner(api)
    pid = world.persona.id

    resp = await api.post(
        f"/api/personas/{pid}/media",
        files={"file": ("test.jpg", io.BytesIO(ONE_PX_JPEG), "image/jpeg")},
        data={"kind": "photo", "caption": "аватар", "tags": "selfie,main"},
        headers=h,
    )
    assert resp.status_code == 201
    item = resp.json()
    assert item["kind"] == "photo" and item["caption"] == "аватар"
    assert item["tags"] == ["selfie", "main"]
    assert item["file_url"].startswith("/api/media/")
    media_id = item["id"]

    listing = (await api.get(f"/api/personas/{pid}/media")).json()
    assert len(listing["items"]) == 1 and listing["items"][0]["id"] == media_id

    assert (await api.delete(f"/api/personas/{pid}/media/{media_id}", headers=h)).status_code == 204
    listing = (await api.get(f"/api/personas/{pid}/media")).json()
    assert len(listing["items"]) == 0


async def test_filter_by_kind(api, world):
    h = await owner(api)
    pid = world.persona.id
    await api.post(
        f"/api/personas/{pid}/media",
        files={"file": ("a.jpg", io.BytesIO(ONE_PX_JPEG), "image/jpeg")},
        data={"kind": "photo"},
        headers=h,
    )
    await api.post(
        f"/api/personas/{pid}/media",
        files={"file": ("b.mp4", io.BytesIO(b"\x00" * 100), "video/mp4")},
        data={"kind": "video_note"},
        headers=h,
    )

    all_items = (await api.get(f"/api/personas/{pid}/media")).json()["items"]
    assert len(all_items) == 2

    photos = (await api.get(f"/api/personas/{pid}/media", params={"kind": "photo"})).json()["items"]
    assert len(photos) == 1 and photos[0]["kind"] == "photo"

    notes = (await api.get(f"/api/personas/{pid}/media", params={"kind": "video_note"})).json()["items"]
    assert len(notes) == 1 and notes[0]["kind"] == "video_note"


async def test_download_file(api, world):
    h = await owner(api)
    pid = world.persona.id
    item = (await api.post(
        f"/api/personas/{pid}/media",
        files={"file": ("test.jpg", io.BytesIO(ONE_PX_JPEG), "image/jpeg")},
        data={"kind": "photo"},
        headers=h,
    )).json()

    resp = await api.get(f"/api/personas/{pid}/media/{item['id']}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")


async def test_reject_unsupported_mime(api, world):
    h = await owner(api)
    resp = await api.post(
        f"/api/personas/{world.persona.id}/media",
        files={"file": ("evil.html", io.BytesIO(b"<html>"), "text/html")},
        data={"kind": "photo"},
        headers=h,
    )
    assert resp.status_code == 422


async def test_reject_invalid_kind(api, world):
    h = await owner(api)
    resp = await api.post(
        f"/api/personas/{world.persona.id}/media",
        files={"file": ("test.jpg", io.BytesIO(ONE_PX_JPEG), "image/jpeg")},
        data={"kind": "sticker"},
        headers=h,
    )
    assert resp.status_code == 422


async def test_delete_nonexistent_returns_404(api, world):
    h = await owner(api)
    assert (await api.delete(f"/api/personas/{world.persona.id}/media/9999", headers=h)).status_code == 404


async def test_repo_by_tags(container, world):
    async with transaction(container.factory) as s:
        from sim.clock import utcnow
        repo = PersonaMediaRepo(s)
        m1 = PersonaMedia(
            persona_id=world.persona.id, kind=MediaKind.PHOTO,
            storage_name="a.jpg", tags=["selfie", "main"], created_at=utcnow(),
        )
        m2 = PersonaMedia(
            persona_id=world.persona.id, kind=MediaKind.VIDEO_NOTE,
            storage_name="b.mp4", tags=["greeting"], created_at=utcnow(),
        )
        await repo.add(m1)
        await repo.add(m2)
        matched = await repo.by_tags(world.persona.id, ["selfie"])
        assert len(matched) == 1 and matched[0].storage_name == "a.jpg"
        matched_all = await repo.by_tags(world.persona.id, [])
        assert len(matched_all) == 2
