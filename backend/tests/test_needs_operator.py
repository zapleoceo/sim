"""needs_operator: filterable in the inbox list and clearable by the operator."""
from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import Dialog
from sim.adapters.db.session import transaction
from tests.conftest import OWNER, inbound, login
from tests.test_ingest import ingest


async def two_dialogs(container, world):
    await ingest(container, world.channel.id, inbound("a", ext="1", thread="t1", user="u1"))
    await ingest(container, world.channel.id, inbound("b", ext="2", thread="t2", user="u2"))
    async with transaction(container.factory) as s:
        dialogs = (await s.scalars(select(Dialog).order_by(Dialog.id))).unique().all()
        dialogs[0].needs_operator = True
        return dialogs[0].id, dialogs[1].id


async def test_filter_returns_only_dialogs_that_need_an_operator(api, container, world):
    flagged, _ = await two_dialogs(container, world)
    await login(api, *OWNER)
    everything = (await api.get("/api/dialogs")).json()
    assert everything["total"] == 2
    assert {r["id"]: r["needs_operator"] for r in everything["items"]}[flagged] is True
    only = (await api.get("/api/dialogs", params={"needs_operator": "true"})).json()
    assert [r["id"] for r in only["items"]] == [flagged] and only["total"] == 1


async def test_resolve_clears_the_flag_but_keeps_the_ai_pause(api, container, world):
    flagged, _ = await two_dialogs(container, world)
    async with transaction(container.factory) as s:
        (await s.get(Dialog, flagged)).ai_paused = True
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    resp = await api.post(f"/api/dialogs/{flagged}/resolve", headers=h)
    assert resp.status_code == 200
    assert resp.json()["needs_operator"] is False and resp.json()["ai_paused"] is True
    assert (await api.get("/api/dialogs", params={"needs_operator": "true"})).json()["total"] == 0
    assert (await api.post("/api/dialogs/9999/resolve", headers=h)).status_code == 404


async def test_resuming_the_ai_also_clears_the_flag(api, container, world):
    flagged, _ = await two_dialogs(container, world)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    resp = await api.post(f"/api/dialogs/{flagged}/ai", json={"paused": False}, headers=h)
    assert resp.json()["needs_operator"] is False
