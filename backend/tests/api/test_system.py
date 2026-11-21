"""Health checks, the SPA fallback and the JSON error shape outside the routers."""
from __future__ import annotations

from pathlib import Path

from litestar.testing import AsyncTestClient

from sim.api.app import create_app
from tests.api.fakes import FakeDeps
from tests.api.helpers import ApiClient


async def test_healthz_and_readyz(client: ApiClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}


async def test_spa_index_assets_and_client_routes(client: ApiClient) -> None:
    for path in ("/", "/chats/5", "/persona"):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert "<title>SIM</title>" in response.text
        assert response.headers["content-type"].startswith("text/html")
    asset = await client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('sim')"


async def test_spa_does_not_serve_files_outside_static_dir(client: ApiClient,
                                                            static_dir: Path) -> None:
    (static_dir.parent / "secret.txt").write_text("nope", encoding="utf-8")
    response = await client.get("/%2e%2e/secret.txt")
    assert "nope" not in response.text


async def test_unknown_api_path_is_json_404(client: ApiClient) -> None:
    response = await client.get("/api/nothing-here")
    assert response.status_code in (401, 404)
    assert "error" in response.json()


async def test_unknown_api_path_after_login_is_404(api: ApiClient) -> None:
    response = await api.get("/api/nothing-here")
    assert response.status_code == 404
    assert "error" in response.json()


async def test_no_spa_without_built_frontend(deps: FakeDeps, tmp_path: Path) -> None:
    deps.settings.static_dir = str(tmp_path / "missing")
    async with AsyncTestClient(app=create_app(deps)) as bare:  # type: ignore[arg-type]
        assert (await bare.get("/")).status_code == 404
        assert (await bare.get("/healthz")).status_code == 200
