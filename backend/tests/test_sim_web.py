"""Browser simulation continuation stays isolated and idempotent (CI-only)."""
from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import Client, Dialog, Message, SimMessage
from sim.worker.loop import Worker
from tests.conftest import OPERATOR, OWNER, decision_json, login


async def test_browser_simulation_continues_without_a_channel(api, container, world, fake_llm):
    csrf = await login(api, OWNER[0])
    headers = {"X-CSRF-Token": csrf}
    started = await api.post(
        "/api/simulations", json={"persona_id": world.persona.id}, headers=headers,
    )
    assert started.status_code == 201, started.text
    branch_id = started.json()["id"]
    fake_llm.replies = [decision_json(reply="Привет из теста", stage="engaged")]
    first = await api.post(
        f"/api/simulations/{branch_id}/messages",
        json={"text": "Тестовый вопрос", "request_id": "browser-1"}, headers=headers,
    )
    duplicate = await api.post(
        f"/api/simulations/{branch_id}/messages",
        json={"text": "Тестовый вопрос", "request_id": "browser-1"}, headers=headers,
    )
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["message"]["id"] == duplicate.json()["message"]["id"]
    assert first.json()["pending"]
    assert (await api.get(f"/api/simulations/{branch_id}")).json()["pending"]
    assert await Worker(container).tick() == 1
    detail = (await api.get(f"/api/simulations/{branch_id}")).json()
    assert not detail["pending"] and detail["can_continue"]
    assert [m["role"] for m in detail["messages"]] == ["client", "ai"]
    assert detail["messages"][1]["text"] == "Привет из теста"
    assert detail["stage"] == "engaged"
    conflict = await api.post(
        f"/api/simulations/{branch_id}/messages",
        json={"text": "Другой текст", "request_id": "browser-1"}, headers=headers,
    )
    assert conflict.status_code == 409
    async with container.factory() as session:
        rows = (await session.scalars(select(SimMessage))).all()
        assert len(rows) == 2
        for model in (Client, Dialog, Message):
            assert not (await session.scalars(select(model))).all()


async def test_browser_simulation_requires_owner_and_branch_author(api, world):
    csrf = await login(api, OWNER[0])
    started = await api.post(
        "/api/simulations", json={"persona_id": world.persona.id},
        headers={"X-CSRF-Token": csrf},
    )
    branch_id = started.json()["id"]
    operator_csrf = await login(api, OPERATOR[0])
    assert (await api.post(
        f"/api/simulations/{branch_id}/messages",
        json={"text": "Не автор", "request_id": "browser-2"},
        headers={"X-CSRF-Token": operator_csrf},
    )).status_code == 403
