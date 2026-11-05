from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import Channel, Dialog, Job, Message
from sim.api.main import create_app
from sim.domain.enums import SessionStatus
from tests.conftest import OWNER, decision_json, inbound, login
from tests.test_ingest import ingest


async def owner(api) -> dict[str, str]:
    return {"X-CSRF-Token": await login(api, *OWNER)}


async def test_health_needs_no_login(api):
    body = (await api.get("/api/health")).json()
    assert body["ok"] is True and body["db"] is True and body["broker"] is True and body["version"]


async def test_requests_without_session_are_401(api):
    for path in ("/api/personas", "/api/dialogs", "/api/clients", "/api/funnel", "/api/content"):
        assert (await api.get(path)).status_code == 401, path


async def test_persona_and_strategy_crud(api):
    h = await owner(api)
    strat = (await api.post("/api/strategies", json={"name": "S", "rules": ["r"],
                                                      "followup_hours": [24, 72]}, headers=h)).json()
    assert strat["reply_delay_s"] == [20.0, 90.0]
    bad = await api.post("/api/strategies", json={"name": "S2", "reply_delay_s": [90, 20]}, headers=h)
    assert bad.status_code == 422
    persona = (await api.post("/api/personas", json={"name": "Марина", "strategy_id": strat["id"]},
                              headers=h)).json()
    assert persona["model"] == "" and persona["strategy_id"] == strat["id"]
    assert (await api.post("/api/personas", json={"name": "Марина"}, headers=h)).status_code == 409
    upd = await api.put(f"/api/personas/{persona['id']}",
                        json={"name": "Марина", "model": "claude-sub/claude-sonnet-5-5"}, headers=h)
    assert upd.json()["model"] == "claude-sub/claude-sonnet-5-5"
    assert len((await api.get("/api/personas")).json()["items"]) == 1
    assert (await api.delete(f"/api/personas/{persona['id']}", headers=h)).status_code == 204
    assert (await api.get(f"/api/personas/{persona['id']}")).status_code == 404


async def test_preview_endpoint(api, world, fake_llm):
    h = await owner(api)
    fake_llm.replies = [decision_json(reply="Привет, я Марина!", stage="engaged")]
    resp = await api.post(
        f"/api/personas/{world.persona.id}/preview",
        json={"message": "Привет", "history": [{"role": "assistant", "text": "Добрый день"}]},
        headers=h,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["reply"] == "Привет, я Марина!" and body["decision"]["stage"] == "engaged"
    assert body["decision"]["reply"] == "Привет, я Марина!" and body["decision"]["refused"] is False
    assert body["model"] == "fake-model" and "latency_ms" in body


async def test_models_endpoint(api):
    await owner(api)
    items = (await api.get("/api/models")).json()["items"]
    assert items[0] == {"id": "claude-sub/claude-sonnet-5-5", "name": "claude-sub/claude-sonnet-5-5",
                        "subscription": True, "available": True}


async def test_channel_lifecycle(api, world, container):
    h = await owner(api)
    ch = (await api.post("/api/channels", json={"kind": "telegram", "persona_id": world.persona.id,
                                                "name": "bot"}, headers=h)).json()
    assert ch["status"] == "new"
    state = await api.post(f"/api/channels/{ch['id']}/connect", json={"bot_token": "123:abc"}, headers=h)
    assert state.json() == {"status": "active"}
    async with container.factory() as s:
        row = await s.get(Channel, ch["id"])
        assert row.status is SessionStatus.ACTIVE and row.account_label == "@fakebot"
        assert "123:abc" not in row.session_enc                      # encrypted at rest
        assert container.box.decrypt_json(row.session_enc)["token"] == "123:abc"
    assert (await api.get(f"/api/channels/{ch['id']}/state")).json()["status"] == "active"
    assert (await api.post(f"/api/channels/{ch['id']}/verify", json={"code": "1"}, headers=h)).status_code == 200
    assert (await api.post("/api/channels", json={"kind": "instagram", "persona_id": world.persona.id},
                           headers=h)).status_code == 422          # no connector registered
    assert (await api.delete(f"/api/channels/{ch['id']}", headers=h)).status_code == 204
    assert all(c["id"] != ch["id"] for c in (await api.get("/api/channels")).json()["items"])


async def test_inbox_flow_operator_takeover_and_stage_rules(api, world, container):
    h = await owner(api)
    await ingest(container, world.channel.id, inbound("Здравствуйте", name="Анна"))
    rows = (await api.get("/api/dialogs", params={"unread_only": "true"})).json()
    assert rows["total"] == 1
    row = rows["items"][0]
    assert row["unread"] == 1 and row["persona"]["name"] == "Марина" and row["channel_kind"] == "telegram"
    assert row["last_message"]["direction"] == "in" and row["client"]["stage"] == "first_contact"
    assert (await api.get("/api/dialogs", params={"q": "Анн"})).json()["total"] == 1
    assert (await api.get("/api/dialogs", params={"q": "zzz"})).json()["total"] == 0

    detail = (await api.get(f"/api/dialogs/{row['id']}")).json()
    assert detail["messages"][0]["text"] == "Здравствуйте" and detail["client"]["channels"][0]["kind"] == "telegram"
    assert (await api.post(f"/api/dialogs/{row['id']}/read", headers=h)).status_code == 204
    assert (await api.get("/api/dialogs", params={"unread_only": "true"})).json()["total"] == 0

    sent = await api.post(f"/api/dialogs/{row['id']}/messages", json={"text": "Я оператор"}, headers=h)
    assert sent.status_code == 201 and sent.json()["author"] == "operator" and sent.json()["delivery"] == "queued"
    async with container.factory() as s:
        assert (await s.scalars(select(Dialog))).unique().one().ai_paused is True
        pending = await s.scalars(select(Job).where(Job.kind == "reply", Job.status == "pending"))
        assert pending.all() == []                                  # AI reply cancelled

    resumed = (await api.post(f"/api/dialogs/{row['id']}/ai", json={"paused": False}, headers=h)).json()
    assert resumed["ai_paused"] is False

    cid = detail["client"]["id"]
    for stage in ("registered", "paid"):
        r = await api.post(f"/api/clients/{cid}/stage", json={"stage": stage}, headers=h)
        assert r.status_code == 422, stage
    ok = await api.post(f"/api/clients/{cid}/stage", json={"stage": "interested", "reason": "call"}, headers=h)
    assert ok.json()["stage"] == "interested"
    patched = await api.patch(f"/api/clients/{cid}", json={"notes": "VIP", "next_step": "позвонить"}, headers=h)
    assert patched.json()["notes"] == "VIP"
    card = (await api.get(f"/api/clients/{cid}")).json()
    assert card["events"][0]["to_stage"] == "interested" and card["events"][0]["type"] == "stage" and card["partner_events"] == []
    assert (await api.get("/api/clients", params={"stage": "interested"})).json()["total"] == 1

    board = (await api.get("/api/funnel")).json()
    interested = next(s for s in board["stages"] if s["stage"] == "interested")
    assert interested["count"] == 1 and board["statuses"]["active"] == 1
    assert [s["stage"] for s in board["stages"]][0] == "first_contact"


async def test_telegram_webhook_secret_and_ingest(api, world, container):
    url = f"/hooks/telegram/{world.channel.id}"
    payload = {"chat": 55, "user": 7, "text": "hello", "id": 1, "name": "Боб"}
    assert (await api.post(url, json=payload)).status_code == 401
    assert (await api.post(url, json=payload, headers={"x-secret": "bad"})).status_code == 401
    ok = await api.post(url, json=payload, headers={"x-secret": "hook-secret"})
    assert ok.status_code == 200 and ok.json()["messages"] == 1
    again = await api.post(url, json=payload, headers={"x-secret": "hook-secret"})
    assert again.status_code == 200
    async with container.factory() as s:
        assert len((await s.scalars(select(Message))).all()) == 1   # deduplicated
    assert (await api.post("/hooks/telegram/9999", json=payload)).status_code == 404
    assert (await api.post(f"/hooks/evolution/{world.channel.id}", json=payload)).status_code == 404


async def test_content_flow(api, world, fake_llm):
    h = await owner(api)
    post = (await api.post("/api/content", json={"persona_id": world.persona.id, "kind": "reel",
                                                  "caption": "идея"}, headers=h)).json()
    assert post["status"] == "draft"
    fake_llm.replies = ["Красивая подпись #стратегия"]
    cap = await api.post(f"/api/content/{post['id']}/generate-caption", headers=h)
    assert cap.json() == {"caption": "Красивая подпись #стратегия"}
    approved = (await api.post(f"/api/content/{post['id']}/approve", headers=h)).json()
    assert approved["status"] == "approved"
    upd = await api.put(f"/api/content/{post['id']}", headers=h, json={
        "persona_id": world.persona.id, "kind": "reel", "caption": "x",
        "scheduled_at": "2030-01-01T10:00:00Z"})
    assert upd.json()["status"] == "scheduled"


async def test_spa_served_when_dist_exists(container, tmp_path):
    import httpx

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SIM</html>")
    (dist / "app.js").write_text("console.log(1)")
    container.settings.static_dir = str(dist)
    app = create_app(container=container)
    app.state.container = container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert "SIM" in (await c.get("/dialogs/12")).text            # SPA fallback
        assert (await c.get("/app.js")).text == "console.log(1)"
        assert (await c.get("/api/nothing")).status_code == 404       # API paths never fall back
        assert (await c.get("/%2e%2e/%2e%2e/etc/passwd")).text.startswith("<html>")
