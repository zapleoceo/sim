from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError

import pytest

from sim import readiness
from sim.api.routers import system


@pytest.mark.parametrize("path", ["/api/ready", "/api/health"])
@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_db_outage_is_503(api, container, monkeypatch, path, failure):
    class BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, *_):
            if failure == "timeout":
                await asyncio.sleep(60)
            raise OSError("private database connection string")

    monkeypatch.setattr(container, "factory", BrokenSession)
    monkeypatch.setattr(system, "READINESS_DB_TIMEOUT_S", 0.02)
    response = await asyncio.wait_for(api.get(path), 1)
    assert response.status_code == 503
    assert response.json()["ok"] is False and response.json()["db"] is False
    assert "private" not in response.text


async def test_liveness_does_not_touch_dependencies(api, container, monkeypatch):
    def forbidden():
        raise AssertionError("liveness must not access the DB")

    monkeypatch.setattr(container, "factory", forbidden)
    response = await api.get("/api/live")
    assert response.status_code == 200 and response.json() == {"ok": True}


async def test_broker_outage_does_not_block_operator_readiness(api, fake_llm, monkeypatch):
    calls = []

    async def unavailable():
        calls.append(True)
        raise OSError("offline")

    monkeypatch.setattr(fake_llm, "list_models", unavailable)
    response = await api.get("/api/ready")
    assert response.status_code == 200 and response.json()["db"] is True
    assert calls == []
    diagnostic = await api.get("/api/health")
    assert diagnostic.status_code == 200 and diagnostic.json()["broker"] is False


@pytest.mark.parametrize(("status", "body", "expected"), [
    (200, '{"ok":true,"db":true}', True),
    (200, '{"ok":false,"db":false}', False),
    (200, '{"ok":true}', False),
    (200, '{"ok":1,"db":"true"}', False),
    (200, '[]', False),
    (200, '<html>not ready</html>', False),
    (503, '{"ok":true,"db":true}', False),
])
def test_shared_gate_rejects_status_and_payload_failures(monkeypatch, status, body, expected):
    class Reply(io.BytesIO):
        pass

    reply = Reply(body.encode())
    reply.status = status

    def open_fixed_url(url, timeout):
        assert url == "http://127.0.0.1:8000/api/ready" and timeout == 4
        return reply

    monkeypatch.setattr(readiness, "urlopen", open_fixed_url)
    assert readiness.check_ready() is expected


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), HTTPError("local", 503, "no", {}, None)])
def test_shared_gate_rejects_connection_failure(monkeypatch, error):
    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(readiness, "urlopen", unavailable)
    assert readiness.check_ready() is False


ROOT = Path(__file__).resolve().parents[2]


def test_image_and_compose_use_shared_readiness_gate():
    assert "CMD python -m sim.readiness" in (ROOT / "backend/Dockerfile").read_text()
    compose = (ROOT / "infra/docker-compose.yml").read_text()
    test_line = next(line for line in compose.splitlines() if 'test: ["CMD", "python"' in line)
    assert json.loads(test_line.split("test:", 1)[1].strip()) == ["CMD", "python", "-m", "sim.readiness"]


@pytest.mark.parametrize(("ready_exit", "http_exit", "expected"), [(0, 0, 0), (1, 0, 1), (0, 22, 1)])
def test_deploy_smoke_requires_both_container_readiness_and_published_port(ready_exit, http_exit, expected):
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    if not Path(bash).is_file():
        pytest.skip("bash unavailable")
    script = (ROOT / "infra/deploy.sh").read_text()
    line = next(line for line in script.splitlines() if line.startswith('ssh "$HOST" \'for i'))
    smoke = line.split("'", 1)[1].rsplit("'", 1)[0]
    # Execute the actual smoke loop with local fakes, no SSH/Docker/network/real sleeps.
    harness = (
        f'docker() {{ [[ "$*" == "exec sim-api python -m sim.readiness" ]] || exit 91; return {ready_exit}; }}; '
        f'curl() {{ [[ "$*" == *"http://127.0.0.1:8010/api/ready"* ]] || exit 92; return {http_exit}; }}; '
        'sleep() { :; }; seq() { echo 1; }; '
    )
    result = subprocess.run([bash, "-c", harness + smoke], capture_output=True, timeout=10, check=False)  # noqa: S603
    assert result.returncode == expected, result.stderr.decode()
