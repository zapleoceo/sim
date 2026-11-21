"""Local media storage: key layout, atomic writes, path traversal, idempotent delete."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sim.adapters.storage import InvalidMediaKey, LocalMediaStorage, MediaNotFound, extension_for
from sim.clock import FixedClock
from sim.ports import PortError


@pytest.fixture
def storage(tmp_path: Path) -> LocalMediaStorage:
    return LocalMediaStorage(str(tmp_path / "media"),
                             clock=FixedClock(datetime(2026, 11, 21, 18, 30, tzinfo=UTC)))


async def test_put_get_round_trip_with_dated_key(storage: LocalMediaStorage):
    key = await storage.put(b"\xff\xd8jpeg", mime="image/jpeg", prefix="library")
    assert re.fullmatch(r"library/2026/11/[0-9a-f]{32}\.jpg", key)
    assert await storage.get(key) == b"\xff\xd8jpeg"
    assert (storage.root / key).read_bytes() == b"\xff\xd8jpeg"


async def test_nested_prefix_and_unknown_type(storage: LocalMediaStorage):
    key = await storage.put(b"x", mime="application/x-strange", prefix="incoming/chat_7")
    assert key.startswith("incoming/chat_7/2026/11/") and key.endswith(".bin")


def test_extensions():
    assert extension_for("audio/ogg; codecs=opus") == "ogg"
    assert extension_for("VIDEO/MP4") == "mp4"
    assert extension_for("text/html") == "bin"


async def test_no_temporary_files_left(storage: LocalMediaStorage):
    key = await storage.put(b"data", mime="video/mp4", prefix="generated")
    folder = (storage.root / key).parent
    assert [p.name for p in folder.iterdir()] == [Path(key).name]


async def test_failed_write_leaves_nothing(storage: LocalMediaStorage, monkeypatch):
    def broken_replace(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        await storage.put(b"data", mime="image/png", prefix="library")
    leftovers = [p for p in storage.root.rglob("*") if p.is_file()]
    assert leftovers == []


@pytest.mark.parametrize("prefix", ["", "../up", "/abs", "a//b", "Library", "a b", "a/../b"])
async def test_bad_prefix_is_refused(storage: LocalMediaStorage, prefix: str):
    with pytest.raises(InvalidMediaKey):
        await storage.put(b"x", mime="image/png", prefix=prefix)


@pytest.mark.parametrize("key", [
    "", "../secret", "library/../../etc/passwd", "/etc/passwd", "library\\..\\x",
    "library/./x.jpg", "library//x.jpg", ".hidden", "library/.x.jpg.1234.part",
])
async def test_traversal_keys_are_refused(storage: LocalMediaStorage, key: str):
    with pytest.raises(InvalidMediaKey):
        await storage.get(key)
    with pytest.raises(InvalidMediaKey):
        await storage.delete(key)


async def test_symlink_out_of_root_is_refused(storage: LocalMediaStorage, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.jpg").write_bytes(b"secret")
    storage.root.mkdir(parents=True)
    try:
        (storage.root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    with pytest.raises(InvalidMediaKey):
        await storage.get("link/f.jpg")


async def test_missing_file_and_idempotent_delete(storage: LocalMediaStorage):
    key = await storage.put(b"x", mime="image/webp", prefix="library")
    await storage.delete(key)
    await storage.delete(key)                      # second delete is not an error
    with pytest.raises(MediaNotFound) as info:
        await storage.get(key)
    assert isinstance(info.value, PortError)
    assert not info.value.retryable
