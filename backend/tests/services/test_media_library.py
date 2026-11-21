from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sim.clock import FixedClock
from sim.db.models import MediaAsset
from sim.domain.enums import (
    Direction,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
)
from sim.services.media_library import (
    MediaNotFound,
    add_media,
    catalog_for_chat,
    delete_media,
    kind_from_mime,
    normalize_tags,
    update_media,
)
from tests.services.conftest import Fakes
from tests.services.factories import add_message, make_chat, make_media, make_persona


@pytest.mark.parametrize(("mime", "kind"), [
    ("image/webp", MediaKind.PHOTO), ("video/mp4", MediaKind.VIDEO),
    ("audio/ogg", MediaKind.AUDIO), ("application/pdf", MediaKind.FILE),
])
def test_kind_from_mime(mime: str, kind: MediaKind) -> None:
    assert kind_from_mime(mime) == kind


def test_normalize_tags() -> None:
    assert normalize_tags([" Селфи ", "#утро", "селфи", "", "дом  у окна"]) == [
        "селфи", "утро", "дом у окна"]


async def test_add_update_delete(db: AsyncSession, fakes: Fakes) -> None:
    persona = await make_persona(db)
    asset = await add_media(db, fakes.storage, fakes.tools, persona_id=persona.id,
                            data=b"JPEG", mime="image/jpeg", file_name="me.jpg",
                            source=MediaSource.LIBRARY, caption=" селфи ", tags=["Утро"],
                            is_reference=True)
    assert asset.id is not None and asset.kind == MediaKind.PHOTO
    assert asset.sha256 == hashlib.sha256(b"JPEG").hexdigest() and asset.size_bytes == 4
    assert asset.caption == "селфи" and asset.tags == ["утро"] and asset.is_reference
    assert fakes.storage.files[asset.storage_key] == b"JPEG"

    updated = await update_media(db, asset.id, caption="у окна", tags=["Окно", "окно"],
                                 is_reference=False)
    assert (updated.caption, updated.tags, updated.is_reference) == ("у окна", ["окно"], False)

    key = asset.storage_key
    await delete_media(db, fakes.storage, asset.id)
    assert await db.get(MediaAsset, asset.id) is None
    assert key not in fakes.storage.files
    with pytest.raises(MediaNotFound):
        await update_media(db, asset.id, caption="x")


async def test_catalog_excludes_media_already_sent_to_this_chat(db: AsyncSession,
                                                                clock: FixedClock) -> None:
    persona = await make_persona(db)
    chat = await make_chat(db, persona)
    other_chat = await make_chat(db, persona, name="Оля")
    sent = await make_media(db, persona.id, caption="отправлено")
    pending = await make_media(db, persona.id, caption="в пути")
    failed = await make_media(db, persona.id, caption="не дошло")
    elsewhere = await make_media(db, persona.id, caption="другому", kind=MediaKind.VIDEO)
    generated = await make_media(db, persona.id, source=MediaSource.GENERATED, caption="сделано")
    await make_media(db, None, source=MediaSource.INCOMING)                 # client's photo
    await make_media(db, persona.id, kind=MediaKind.AUDIO)                  # voice note
    for media, status in ((sent, MessageStatus.SENT), (pending, MessageStatus.PENDING),
                          (failed, MessageStatus.FAILED)):
        await add_message(db, chat, "", at=clock.now(), direction=Direction.OUT,
                          kind=MessageKind.PHOTO, media=media, status=status)
    await add_message(db, other_chat, "", at=clock.now(), direction=Direction.OUT,
                      kind=MessageKind.VIDEO, media=elsewhere)

    available, already = await catalog_for_chat(db, persona.id, chat.id)

    assert [a.id for a in available] == [failed.id, elsewhere.id, generated.id]
    assert sorted(a.id for a, _ in already) == sorted([sent.id, pending.id])
    assert all(at == clock.now() for _, at in already)
