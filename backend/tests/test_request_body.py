"""The system log keeps a masked, bounded request body for webhooks and writes (never sign-in)."""
from __future__ import annotations

import json

from sqlalchemy import select

from sim.adapters.db.models import TraceEvent
from sim.services import request_body
from sim.services.request_body import LIMIT, NOT_KEPT, body_out, capture, is_secret_key, redact
from tests.conftest import OWNER, login

HOOK_HEADERS = {"x-secret": "hook-secret"}


async def steps(container, kind: str) -> list[TraceEvent]:
    async with container.factory() as s:
        rows = await s.scalars(select(TraceEvent).where(TraceEvent.kind == kind).order_by(TraceEvent.id))
        return list(rows.all())


def test_secret_keys_are_hidden_at_any_depth_and_plain_keys_stay():
    for name in ("password", "apiKey", "X-Api-Key", "access_token", "phone_code_hash", "code",
                 "sessionString", "csrf", "Authorization", "cookie", "client_secret", "otp"):
        assert is_secret_key(name), name
    for name in ("text", "chat", "inline_keyboard", "language_code", "name", "monkey"):
        assert not is_secret_key(name), name
    body = {"user": {"name": "Боб", "auth": {"password": "p1", "token": "t1"}},
            "items": [{"secret": "s1", "text": "hi"}]}
    out = redact(body)
    assert out == {"user": {"name": "Боб", "auth": "[REDACTED]"},
                   "items": [{"secret": "[REDACTED]", "text": "hi"}]}


def test_capture_masks_values_and_cuts_by_the_limit():
    raw = json.dumps({"note": "bot 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", "n": 1}).encode()
    kept = capture(raw, "application/json")
    assert kept["truncated"] is False and kept["content"]["n"] == 1
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in json.dumps(kept)

    big = json.dumps({"text": "x" * 60_000}).encode()
    cut = capture(big, "application/json")
    assert cut["truncated"] is True and len(cut["content"]) == LIMIT

    form = capture(b"name=Bob&password=hunter2", "application/x-www-form-urlencoded")
    assert form["content"] == {"name": "Bob", "password": "[REDACTED]"}
    assert capture(b"plain text", "text/plain")["content"] == "plain text"


def test_projection_hides_secrets_again_and_passes_reasons():
    assert body_out(None) is None and body_out({"x": 1}) is None
    assert body_out({"omitted": "тело пустое"}) == {"omitted": "тело пустое"}
    out = body_out({"content": {"token": "old-row-secret", "a": 1}, "truncated": False})
    assert out == {"content": {"token": "[REDACTED]", "a": 1}, "truncated": False, "redacted": True}


async def test_webhook_body_is_kept_masked_and_the_route_still_reads_it(api, container, world):
    payload = {"chat": 55, "user": 7, "text": "Привет", "id": 1, "name": "Боб",
               "meta": {"api_key": "K-123", "nested": [{"session": "S-1", "ok": True}]}}
    resp = await api.post(f"/hooks/telegram/{world.channel.id}", headers=HOOK_HEADERS, json=payload)
    assert resp.status_code == 200
    (inbound,) = await steps(container, "inbound")  # the route parsed the body after the middleware
    assert inbound.status == "ok" and inbound.message_id
    (hook,) = await steps(container, "webhook")
    kept = hook.detail["request_body"]
    assert kept["truncated"] is False and kept["content"]["text"] == "Привет"
    assert kept["content"]["meta"] == {"api_key": "[REDACTED]", "nested": [{"session": "[REDACTED]", "ok": True}]}
    assert "K-123" not in json.dumps(hook.detail) and "S-1" not in json.dumps(hook.detail)

    h = {"X-CSRF-Token": await login(api, *OWNER)}
    trace = (await api.get(f"/api/activity/traces/{resp.headers['X-Trace-Id']}", headers=h)).json()
    step = next(s for s in trace["steps"] if s["kind"] == "webhook")
    assert step["request_body"]["content"]["text"] == "Привет" and step["request_body"]["redacted"] is True
    assert "request_body" not in step["detail"]
    page = (await api.get("/api/activity/log", headers=h)).json()
    assert all("request_body" not in item["detail"] for item in page["items"])


async def test_sign_in_bodies_are_never_kept(api, container):
    await login(api, *OWNER)
    (auth,) = [e for e in await steps(container, "http") if e.name == "POST /api/auth/telegram"]
    assert auth.detail == {"request_body": {"omitted": NOT_KEPT}}
    assert "hash" not in json.dumps(auth.detail)
    await api.post("/api/oauth/token", data={"code": "abc", "client_secret": "zzz"})
    oauth = [e for e in await steps(container, "http") if e.name == "POST /api/oauth/token"]
    assert oauth and all(e.detail == {"request_body": {"omitted": NOT_KEPT}} for e in oauth)


async def test_files_are_described_not_kept_and_gets_carry_no_body(api, container):
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    await api.post("/api/content", headers=h, files={"file": ("a.bin", b"\x00\x01secret-bytes")})
    (upload,) = [e for e in await steps(container, "http") if e.name == "POST /api/content"]
    reason = upload.detail["request_body"]["omitted"]
    assert reason.startswith("multipart/form-data, ") and "байт" in reason
    assert "secret-bytes" not in json.dumps(upload.detail)

    await api.get("/api/clients", headers=h)
    gets = [e for e in await steps(container, "http") if e.name == "GET /api/clients"]
    assert gets and all("request_body" not in (e.detail or {}) for e in gets)
    assert request_body.wants_body("GET", "/hooks/x") and not request_body.wants_body("GET", "/api/x")
