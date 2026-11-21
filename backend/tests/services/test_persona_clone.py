"""«Клонировать»: one test per part (fields, arcs, canon, media files with new keys), what is
never copied, the name check and cleaning up copied files when the clone fails."""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Channel, Client, MediaAsset, Persona, PersonaArc, PersonaCanon
from sim.domain.enums import ArcStatus, CanonStatus, ClonePart, MediaKind, MediaSource
from sim.services.persona_clone import (
    COPIERS,
    DEFAULT_PARTS,
    FieldsCopier,
    PersonaNameTaken,
    clone_persona,
)
from tests.fakes.ai import MemoryStorage
from tests.services.factories import make_chat, make_media, make_persona

LIFE = {"routine": [], "people": [{"name": "Андрей", "relation": "муж", "traits": "",
                                    "contact": "", "notes": ""}], "hobbies": [], "places": []}


async def source(db: AsyncSession, **fields: Any) -> Persona:
    return await make_persona(
        db, timezone="Europe/Berlin", language="uk", birth_date=date(1990, 1, 2),
        appearance="green eyes", life=LIFE, model_life="life-model",
        model_passport="passport-model", reply_delay_s=20, video_enabled=False, **fields)


def fields_of(part: ClonePart) -> tuple[str, ...]:
    copier = COPIERS[part]
    assert isinstance(copier, FieldsCopier)
    return copier.fields


@pytest.mark.parametrize("part", [p for p in ClonePart if isinstance(COPIERS[p], FieldsCopier)])
async def test_each_field_part_copies_only_its_fields(db: AsyncSession, part: ClonePart) -> None:
    src = await source(db)
    clone = await clone_persona(db, MemoryStorage(), src.id, "Оля", [part])
    blank = Persona(name="blank")
    db.add(blank)
    await db.flush()
    for name in fields_of(part):
        assert getattr(clone, name) == getattr(src, name), name
    others = {f for p in ClonePart if isinstance(COPIERS[p], FieldsCopier) for f in fields_of(p)}
    for name in others - set(fields_of(part)):
        assert getattr(clone, name) == getattr(blank, name), name


def test_every_part_has_a_copier_and_the_canon_is_off_by_default() -> None:
    assert set(COPIERS) == set(ClonePart)
    assert ClonePart.CANON not in DEFAULT_PARTS and len(DEFAULT_PARTS) == len(ClonePart) - 1


async def test_world_is_a_deep_copy(db: AsyncSession) -> None:
    src = await source(db)
    clone = await clone_persona(db, MemoryStorage(), src.id, "Оля", [ClonePart.WORLD])
    assert clone.life == LIFE
    clone.life["people"][0]["name"] = "Пётр"
    assert src.life["people"][0]["name"] == "Андрей"


async def test_arcs_and_canon(db: AsyncSession) -> None:
    src = await source(db)
    db.add_all([
        PersonaArc(persona_id=src.id, title="Ремонт", summary="ванная", stage="плитка",
                   status=ArcStatus.PAUSED, started_on=date(2026, 11, 1)),
        PersonaCanon(persona_id=src.id, fact="Собаку зовут Рекс", status=CanonStatus.ACTIVE),
        PersonaCanon(persona_id=src.id, fact="отклонено", status=CanonStatus.REJECTED),
    ])
    await db.flush()
    without = await clone_persona(db, MemoryStorage(), src.id, "Без канона", [ClonePart.ARCS])
    [arc] = await db.scalars(select(PersonaArc).where(PersonaArc.persona_id == without.id))
    assert (arc.title, arc.stage, arc.status) == ("Ремонт", "плитка", ArcStatus.PAUSED)
    assert list(await db.scalars(select(PersonaCanon).where(
        PersonaCanon.persona_id == without.id))) == []

    full = await clone_persona(db, MemoryStorage(), src.id, "С каноном", [ClonePart.CANON])
    facts = list(await db.scalars(select(PersonaCanon).where(
        PersonaCanon.persona_id == full.id)))
    assert [(f.fact, f.chat_id) for f in facts] == [("Собаку зовут Рекс", None)]


async def test_media_files_are_copied_to_new_keys(db: AsyncSession) -> None:
    storage = MemoryStorage()
    src = await source(db)
    face = await make_media(db, src.id, caption="лицо", tags=["селфи"], is_reference=True,
                            storage=storage, data=b"FACE")
    await make_media(db, src.id, kind=MediaKind.VIDEO, caption="танец", storage=storage,
                     data=b"VIDEO")
    await make_media(db, src.id, source=MediaSource.GENERATED, storage=storage, data=b"GEN")
    src.avatar_media_id = face.id
    await db.flush()

    clone = await clone_persona(db, storage, src.id, "Оля", [ClonePart.MEDIA])

    copies = list(await db.scalars(select(MediaAsset).where(MediaAsset.persona_id == clone.id)
                                   .order_by(MediaAsset.id)))
    assert [(m.caption, m.kind, m.is_reference, m.tags) for m in copies] == [
        ("лицо", MediaKind.PHOTO, True, ["селфи"]), ("танец", MediaKind.VIDEO, False, [])]
    assert all(m.source == MediaSource.LIBRARY for m in copies)     # generated never copied
    assert copies[0].storage_key != face.storage_key
    assert await storage.get(copies[0].storage_key) == b"FACE"
    assert await storage.get(copies[1].storage_key) == b"VIDEO"
    assert clone.avatar_media_id == copies[0].id
    # Deleting the copy's file does not touch the source.
    await storage.delete(copies[0].storage_key)
    assert await storage.get(face.storage_key) == b"FACE"


async def test_channels_clients_and_days_are_never_copied(db: AsyncSession) -> None:
    src = await source(db)
    await make_chat(db, src)
    clone = await clone_persona(db, MemoryStorage(), src.id, "Оля", list(ClonePart))
    for model in (Channel, Client):
        rows = list(await db.scalars(select(model).where(model.persona_id == clone.id)))
        assert rows == [], model
    assert clone.is_active and clone.name == "Оля"


async def test_name_must_be_free_and_source_must_exist(db: AsyncSession) -> None:
    src = await source(db)
    with pytest.raises(PersonaNameTaken):
        await clone_persona(db, MemoryStorage(), src.id, " Марина ", [])
    with pytest.raises(LookupError):
        await clone_persona(db, MemoryStorage(), 999_999, "Оля", [])


class BrokenStorage(MemoryStorage):
    """Copies the first file, then fails."""

    async def put(self, data: bytes, *, mime: str, prefix: str) -> str:
        if any(key.startswith("library/") for key in self.files if key.endswith("copy")):
            raise OSError("disk full")
        key = await super().put(data, mime=mime, prefix=prefix)
        self.files[f"{key}-copy"] = self.files.pop(key)
        return f"{key}-copy"


async def test_a_failed_clone_deletes_the_copied_files(db: AsyncSession) -> None:
    storage = BrokenStorage()
    src = await source(db)
    for data in (b"ONE", b"TWO"):
        key = f"library/{data.decode()}"
        storage.files[key] = data
        db.add(MediaAsset(persona_id=src.id, source=MediaSource.LIBRARY,
                          kind=MediaKind.PHOTO, storage_key=key, mime="image/jpeg",
                          sha256="0" * 64, size_bytes=3))
    await db.flush()
    with pytest.raises(OSError, match="disk full"):
        await clone_persona(db, storage, src.id, "Оля", [ClonePart.MEDIA])
    assert sorted(storage.files) == ["library/ONE", "library/TWO"]
