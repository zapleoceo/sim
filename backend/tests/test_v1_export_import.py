"""v1 -> v2 transfer (scripts/export_v1.py, scripts/import_v1.py): pure mapping and an idempotent
import into the disposable database."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from sim.adapters.storage import LocalMediaStorage
from sim.crypto import decrypt_json, encrypt_json
from sim.db.models import AdminUser, Channel, MediaAsset, Persona
from sim.domain.enums import ChannelStatus, MediaKind, MediaSource

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
KEY = Fernet.generate_key().decode()


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look the module up while building the class
    spec.loader.exec_module(module)
    return module


export_v1 = load_script("export_v1")
import_v1 = load_script("import_v1")

V1_SESSION = {"api_id": 2040, "api_hash": "b18441a1ff607e10a989891a5462e627",
              "string_session": "1ApWapzMBu...", "account_label": "@marina_sv", "pending": None}


def v1_rows() -> dict:
    return {
        "persona": {"id": 1, "name": "Марина Светлова", "agent_prompt": "Ты Марина.",
                    "voice_id": "Vindemiatrix", "voice_style": "тепло", "timezone": "Europe/Kyiv",
                    "birth_date": date(1999, 4, 12), "gender": "", "language": "ru",
                    "model": "gemini-3-pro"},
        "guard_prompt": "Не признавайся, что ты ИИ.",
        "channel": {"id": 3, "kind": "telegram_user", "name": "Марина TG",
                    "account_label": "@marina_sv", "status": "active",
                    "session_enc": encrypt_json(V1_SESSION, KEY)},
        "admins": [{"telegram_id": 169510539, "display_name": "Дима", "tg_username": "dima"}],
        "media": [
            {"id": 5, "kind": "photo", "storage_name": "AbCdEfGh1234.jpg", "caption": "селфи",
             "tags": ["селфи", "дом"]},
            {"id": 6, "kind": "video_note", "storage_name": "ZyXwVuTs9876.mp4", "caption": "",
             "tags": None},
        ],
    }


def export_document() -> dict:
    rows = v1_rows()
    document = export_v1.build_export(
        rows["persona"], rows["guard_prompt"], rows["channel"], rows["admins"], rows["media"],
        exported_at=datetime(2026, 11, 21, 12, 0, tzinfo=UTC))
    return json.loads(json.dumps(document))     # exactly what the file round trip gives


# --- export ----------------------------------------------------------------------------------


def test_export_document_shape():
    document = export_document()
    assert document["format"] == "sim-v1-export" and document["version"] == 1
    assert document["persona"]["prompt"] == "Ты Марина."
    assert document["persona"]["birth_date"] == "1999-04-12"
    assert document["guard_prompt"] == "Не признавайся, что ты ИИ."
    assert document["channel"]["status"] == "active"
    assert decrypt_json(document["channel"]["session_enc"], KEY) == V1_SESSION   # copied as is
    assert document["admins"] == [{"telegram_id": 169510539, "name": "Дима", "username": "dima"}]
    assert document["media"][1]["tags"] == []


def test_export_without_channel_or_guard():
    rows = v1_rows()
    document = export_v1.build_export(rows["persona"], None, None, [], [],
                                      exported_at=datetime(2026, 11, 21, tzinfo=UTC))
    assert document["channel"] is None and document["guard_prompt"] is None
    assert document["admins"] == [] and document["media"] == []


@pytest.mark.parametrize(("url", "expected"), [
    ("postgresql+asyncpg://sim:pw@postgres:5432/sim", "postgresql://sim:pw@postgres:5432/sim"),
    ("postgresql+psycopg://sim@db/sim", "postgresql://sim@db/sim"),
    ("postgres://sim@db/sim", "postgresql://sim@db/sim"),
    ("postgresql://sim@db/sim", "postgresql://sim@db/sim"),
])
def test_libpq_url(url, expected):
    assert export_v1.libpq_url(url) == expected


def test_export_file_is_private(tmp_path):
    out = tmp_path / "v1.json"
    export_v1.write_private(out, export_document())
    assert json.loads(out.read_text(encoding="utf-8"))["persona"]["name"] == "Марина Светлова"
    if sys.platform != "win32":
        assert out.stat().st_mode & 0o777 == 0o600


# --- import: pure mapping --------------------------------------------------------------------


def test_load_export_validates_format():
    raw = json.dumps(export_document())
    assert import_v1.load_export(raw)["persona"]["name"] == "Марина Светлова"
    for broken in ({"format": "other"}, {**export_document(), "version": 2},
                   {**export_document(), "persona": {}}):
        with pytest.raises(import_v1.ExportFormatError):
            import_v1.load_export(json.dumps(broken))


def test_persona_fields():
    fields = import_v1.persona_fields(export_document())
    assert fields == {
        "name": "Марина Светлова", "prompt": "Ты Марина.", "voice_id": "Vindemiatrix",
        "voice_style": "тепло", "gender": "female", "language": "ru",
        "timezone": "Europe/Kyiv", "birth_date": date(1999, 4, 12),
        "guard_rules": "Не признавайся, что ты ИИ.",
    }
    assert "model_reply" not in fields          # models are picked on the persona page


def test_persona_fields_keep_defaults_for_empty_values():
    document = export_document()
    document["persona"].update(timezone="", birth_date=None)
    document["guard_prompt"] = None
    fields = import_v1.persona_fields(document)
    assert "timezone" not in fields and "birth_date" not in fields
    assert "guard_rules" not in fields


@pytest.mark.parametrize(("v1", "v2"), [
    ("active", ChannelStatus.CONNECTED), ("expired", ChannelStatus.ERROR),
    ("error", ChannelStatus.ERROR), ("challenge", ChannelStatus.NEW), ("", ChannelStatus.NEW),
])
def test_channel_status(v1, v2):
    assert import_v1.channel_status(v1) is v2


def test_v2_credentials():
    assert import_v1.v2_credentials(V1_SESSION) == {
        "api_id": 2040, "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session": "1ApWapzMBu...", "proxy": ""}
    with_proxy = import_v1.v2_credentials({**V1_SESSION, "api_id": "2040",
                                           "proxy": " socks5://u:p@h:1080 "})
    assert with_proxy["api_id"] == 2040 and with_proxy["proxy"] == "socks5://u:p@h:1080"


@pytest.mark.parametrize("broken", [
    {"api_id": 0, "api_hash": "h", "string_session": "s"},
    {"api_id": "x", "api_hash": "h", "string_session": "s"},
    {"api_id": 1, "api_hash": "", "string_session": "s"},
    {"pending": {"step": "code"}, "api_id": 1, "api_hash": "h"},
])
def test_v2_credentials_need_a_finished_login(broken):
    with pytest.raises(import_v1.ExportFormatError):
        import_v1.v2_credentials(broken)


def test_media_items():
    photo, note = import_v1.media_items(export_document())
    assert (photo.kind, photo.mime, photo.is_reference) == (MediaKind.PHOTO, "image/jpeg", True)
    assert photo.tags == ["селфи", "дом"]
    assert (note.kind, note.mime, note.is_reference) == (MediaKind.VIDEO, "video/mp4", False)


@pytest.mark.parametrize("name", ["../../etc/passwd", "short.jpg", "a/b/cdefghij.jpg"])
def test_media_items_refuse_unsafe_names(name):
    document = export_document()
    document["media"][0]["storage_name"] = name
    with pytest.raises(import_v1.ExportFormatError):
        import_v1.media_items(document)


def test_read_media(tmp_path):
    (tmp_path / "AbCdEfGh1234.jpg").write_bytes(b"jpeg")
    assert import_v1.read_media(tmp_path, "AbCdEfGh1234.jpg") == b"jpeg"
    assert import_v1.read_media(tmp_path, "Missing12345.jpg") is None
    assert import_v1.read_media(tmp_path, "../AbCdEfGh1234.jpg") is None


# --- import into the database ----------------------------------------------------------------


async def test_import_is_idempotent(db, tmp_path):
    v1_media = tmp_path / "v1-media"
    v1_media.mkdir()
    (v1_media / "AbCdEfGh1234.jpg").write_bytes(b"\xff\xd8photo")   # the video note is missing
    storage = LocalMediaStorage(str(tmp_path / "media"))
    db.add(Persona(name="Марина"))                  # what sim.seed creates on an empty database
    await db.flush()
    document = export_document()

    first = await import_v1.import_export(db, document, media_dir=v1_media, storage=storage,
                                          secret_keys=KEY)
    second = await import_v1.import_export(db, document, media_dir=v1_media, storage=storage,
                                           secret_keys=KEY)

    assert (first.persona_created, first.channel, first.media_added) == (False, "created", 1)
    assert first.media_missing == ["ZyXwVuTs9876.mp4"]
    assert (second.channel, second.media_added, second.media_skipped) == ("updated", 0, 1)
    assert second.persona_id == first.persona_id

    personas = (await db.scalars(select(Persona))).all()
    assert [p.name for p in personas] == ["Марина Светлова"]
    persona = personas[0]
    assert persona.prompt == "Ты Марина." and persona.voice_id == "Vindemiatrix"
    assert persona.birth_date == date(1999, 4, 12) and persona.gender == "female"
    assert persona.guard_rules == "Не признавайся, что ты ИИ."
    assert persona.model_reply == ""

    channels = (await db.scalars(select(Channel))).all()
    assert len(channels) == 1
    channel = channels[0]
    assert channel.status is ChannelStatus.CONNECTED and channel.account_label == "@marina_sv"
    assert decrypt_json(channel.credentials_enc or "", KEY) == {
        "api_id": 2040, "api_hash": "b18441a1ff607e10a989891a5462e627",
        "session": "1ApWapzMBu...", "proxy": ""}

    admins = (await db.scalars(select(AdminUser))).all()
    assert [(a.telegram_id, a.name, a.is_active) for a in admins] == [(169510539, "Дима", True)]

    media = (await db.scalars(select(MediaAsset))).all()
    assert len(media) == 1
    asset = media[0]
    assert (asset.source, asset.kind, asset.is_reference) == (
        MediaSource.LIBRARY, MediaKind.PHOTO, True)
    assert asset.caption == "селфи" and asset.tags == ["селфи", "дом"]
    assert await storage.get(asset.storage_key) == b"\xff\xd8photo"
    assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 1


async def test_import_without_session_skips_the_channel(db, tmp_path):
    document = export_document()
    document["channel"]["session_enc"] = None
    report = await import_v1.import_export(
        db, document, media_dir=tmp_path, storage=LocalMediaStorage(str(tmp_path / "m")),
        secret_keys=KEY)
    assert report.channel.startswith("skipped")
    assert await db.scalar(select(func.count()).select_from(Channel)) == 0
