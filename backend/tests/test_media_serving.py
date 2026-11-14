"""/api/media never serves a stored file as a page (stored XSS through uploads)."""
from __future__ import annotations

import pytest

from sim.adapters.media import safe_suffix, serving
from tests.conftest import OWNER, login


@pytest.mark.parametrize("given,kept", [
    ("ogg", "ogg"), ("JPG", "jpg"), ("webm", "webm"), ("txt", "txt"),
    ("html", "bin"), ("htm", "bin"), ("svg", "bin"), ("xml", "bin"), ("js", "bin"), ("", "bin"),
])
def test_only_known_suffixes_are_stored(given, kept):
    assert safe_suffix(given) == kept


def test_only_audio_images_and_video_are_inline():
    assert serving("a.ogg") == ("audio/ogg", True)
    assert serving("a.png") == ("image/png", True)
    assert serving("a.txt") == ("application/octet-stream", False)
    assert serving("a.bin") == ("application/octet-stream", False)


async def test_media_responses_are_sandboxed(api, container):
    await login(api, *OWNER)
    page = await container.media.save(b"<script>alert(1)</script>", "html")
    assert page.endswith(".bin")
    r = await api.get(f"/api/media/{page}")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    voice = await container.media.save(b"OggS", "ogg")
    v = await api.get(f"/api/media/{voice}")
    assert v.headers["content-type"].startswith("audio/ogg") and "attachment" not in v.headers.get("content-disposition", "")
