"""Her life on the dashboard: the world, story arcs, planned days, canon, the planner model and
the appearance description (docs/api.md, «Жизнь персонажа»)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import LlmCall, MediaAsset, Persona, PersonaCanon, PersonaDay
from sim.domain.enums import CanonStatus, LlmPurpose, MediaKind, MediaSource
from tests.api.fakes import PNG, FakeDeps
from tests.api.helpers import ApiClient, World
from tests.services.life_seed import SEED, day_plan

EVENTS = [e.model_dump(mode="json") for e in day_plan()]


async def test_life_round_trip_and_validation(api: ApiClient, world: World,
                                              db: AsyncSession) -> None:
    empty = await api.get("/api/persona/life")
    assert empty.status_code == 200
    assert empty.json() == {"routine": [], "people": [], "hobbies": [], "places": []}

    saved = await api.put("/api/persona/life", json=SEED["life"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["routine"][0]["activity"] == "сон, телефон в другой комнате"
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None and len(persona.life["people"]) == 2
    assert (await api.get("/api/persona/life")).json() == saved.json()

    bad = await api.put("/api/persona/life", json={"routine": [{"days": [9], "start": "1:00",
                                                                "end": "2:00", "activity": ""}]})
    assert bad.status_code == 400 and bad.json()["error"].startswith("Неверные данные")


async def test_arcs_crud(api: ApiClient, world: World) -> None:
    created = await api.post("/api/persona/arcs", json={
        "title": "Ремонт в ванной", "summary": "Меняют ванну на душ", "stage": "плитка"})
    assert created.status_code == 201, created.text
    arc = created.json()
    assert arc["status"] == "active" and arc["started_on"] == "2026-11-21"
    patched = await api.patch(f"/api/persona/arcs/{arc['id']}",
                              json={"stage": " мастер пришёл ", "status": "paused"})
    assert patched.json()["stage"] == "мастер пришёл" and patched.json()["status"] == "paused"
    assert (await api.patch(f"/api/persona/arcs/{arc['id']}",
                            json={"status": "lost"})).status_code == 400
    listed = (await api.get("/api/persona/arcs")).json()["items"]
    assert [a["title"] for a in listed] == ["Ремонт в ванной"]
    assert (await api.delete(f"/api/persona/arcs/{arc['id']}")).status_code == 204
    assert (await api.get("/api/persona/arcs")).json()["items"] == []
    assert (await api.delete(f"/api/persona/arcs/{arc['id']}")).status_code == 404


async def test_days_edit_and_list(api: ApiClient, world: World, db: AsyncSession) -> None:
    listed = await api.get("/api/persona/days")
    body = listed.json()
    assert body["today"] == "2026-11-21" and body["items"] == []
    assert body["now_minute"] == 20 * 60 + 30                # 18:30 UTC in Kyiv

    saved = await api.put("/api/persona/days/2026-11-21", json={"events": EVENTS,
                                                                "summary": "хороший день"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "edited" and len(saved.json()["events"]) == 7
    [row] = (await db.scalars(select(PersonaDay))).all()
    assert row.summary == "хороший день"
    items = (await api.get("/api/persona/days", params={"from": "2026-11-20",
                                                        "to": "2026-11-22"})).json()["items"]
    assert [d["day"] for d in items] == ["2026-11-21"]

    overlap = [EVENTS[0], {**EVENTS[1], "start": "07:30"}]
    bad = await api.put("/api/persona/days/2026-11-21", json={"events": overlap})
    assert bad.status_code == 400 and "наложений" in bad.json()["error"]
    wrong = await api.put("/api/persona/days/2026-11-21", json={"events": [{"start": "x"}]})
    assert wrong.status_code == 400
    assert (await api.get("/api/persona/days", params={"from": "2026-11-22",
                                                       "to": "2026-11-01"})).status_code == 400


async def test_plan_now_needs_the_life_model_and_queues_a_task(api: ApiClient, world: World,
                                                               deps: FakeDeps) -> None:
    refused = await api.post("/api/persona/days/2026-11-22/plan")
    assert refused.status_code == 400 and "Жизнь" in refused.json()["error"]
    assert (await api.put("/api/persona", json={"model_life": "google/life"})).json()[
        "model_life"] == "google/life"
    queued = await api.post("/api/persona/days/2026-11-22/plan")
    assert queued.status_code == 202 and queued.json() == {"queued": True, "day": "2026-11-22"}
    assert [c[:2] for c in deps.tasks.calls if c[0] == "plan_day"] == [
        ("plan_day", (world.persona_id, "2026-11-22", True))]


async def test_canon_list_edit_and_reject(api: ApiClient, world: World,
                                          db: AsyncSession) -> None:
    fact = PersonaCanon(persona_id=world.persona_id, fact="Соседскую собаку зовут Рекс",
                        chat_id=world.chat_id, event_id=world.event_id,
                        status=CanonStatus.ACTIVE)
    db.add(fact)
    await db.commit()
    items = (await api.get("/api/persona/canon")).json()["items"]
    assert [(i["fact"], i["status"], i["chat_id"]) for i in items] == [
        ("Соседскую собаку зовут Рекс", "active", world.chat_id)]
    edited = await api.patch(f"/api/persona/canon/{fact.id}",
                             json={"fact": "Соседского пса  зовут Рекс", "status": "rejected"})
    assert edited.json()["fact"] == "Соседского пса зовут Рекс"
    assert edited.json()["status"] == "rejected"
    active = (await api.get("/api/persona/canon", params={"status": "active"})).json()
    assert active["items"] == []
    assert (await api.patch("/api/persona/canon/999999", json={})).status_code == 404


async def test_persona_has_life_fields(api: ApiClient, world: World) -> None:
    body = (await api.get("/api/persona")).json()
    assert body["model_life"] == "" and body["appearance"] == ""
    assert body["voice_ambience_enabled"] is True
    saved = (await api.put("/api/persona", json={
        "appearance": "green eyes", "voice_ambience_enabled": False})).json()
    assert saved["appearance"] == "green eyes" and saved["voice_ambience_enabled"] is False
    assert "life" not in body


async def reference(db: AsyncSession, deps: FakeDeps, world: World, **fields: Any) -> None:
    key = await deps.storage.put(PNG, mime="image/png", prefix="library")
    db.add(MediaAsset(persona_id=world.persona_id, source=MediaSource.LIBRARY,
                      kind=MediaKind.PHOTO, storage_key=key, mime="image/png",
                      sha256="0" * 64, size_bytes=len(PNG), **fields))
    await db.commit()


async def test_describe_appearance_by_the_references(api: ApiClient, world: World,
                                                     db: AsyncSession, deps: FakeDeps) -> None:
    no_model = await api.post("/api/persona/appearance/describe")
    assert no_model.status_code == 400
    persona = await db.get(Persona, world.persona_id)
    assert persona is not None
    persona.model_vision = "google/vision"
    await db.commit()
    no_refs = await api.post("/api/persona/appearance/describe")
    assert no_refs.status_code == 400 and "референс" in no_refs.json()["error"]

    await reference(db, deps, world, is_reference=True)
    await reference(db, deps, world, is_reference=False)
    deps.llm.answer = "green eyes,\n long auburn hair, slim build"
    described = await api.post("/api/persona/appearance/describe")
    assert described.status_code == 200, described.text
    assert described.json() == {"text": "green eyes, long auburn hair, slim build",
                                "references": 1}
    [chat] = deps.llm.chats
    parts = chat["messages"][0].parts
    assert chat["model"] == "google/vision" and parts[1].data == PNG
    [log] = (await db.scalars(select(LlmCall).where(LlmCall.purpose == LlmPurpose.VISION)))
    assert log.request["task"] == "appearance"
    assert (await db.get(Persona, world.persona_id)).appearance == ""  # type: ignore[union-attr]
