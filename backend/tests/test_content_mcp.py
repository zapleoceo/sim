"""Offline MCP client, fake transports and synthetic content only."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import AdminUser, ContentPost, Dialog, Job, McpAudit, McpGrant
from sim.adapters.db.session import transaction
from sim.domain.enums import ContentStatus, Role
from tests.conftest import OPERATOR, OWNER, login
from tests.test_calls import chatting_world

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


async def grant(api, scopes, did=None, pid=None):
    me = await api.get("/api/auth/me")
    csrf = me.json()["csrf_token"] if me.status_code == 200 else await login(api, *OWNER)
    response = await api.post("/api/mcp/grants", headers={"X-CSRF-Token": csrf}, json={
        "scopes": scopes, "dialog_ids": [did] if did else [], "post_ids": [pid] if pid else []})
    assert response.status_code == 200, response.text
    data = response.json()
    return data, {**HEADERS, "Authorization": "Bearer " + data["token"]}


async def rpc(api, headers, method, params=None):
    return await api.post("/api/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


async def call(api, headers, name, **arguments):
    response = await rpc(api, headers, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def value(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


async def test_protocol_scoped_read_without_side_effects(api, container, world):
    did = await chatting_world(container, world)
    data, headers = await grant(api, ["dialog:read", "trace:read"], did)
    async with container.factory() as s:
        unread = (await s.get(Dialog, did)).unread
        jobs = list(await s.scalars(select(Job.id)))
    initialized = (await rpc(api, headers, "initialize")).json()["result"]
    assert initialized["protocolVersion"] == "2025-06-18"
    response = await api.post("/api/mcp", headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
    tools = (await rpc(api, headers, "tools/list")).json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"read_dialog", "read_checks"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    page = value(await call(api, headers, "read_dialog", dialog_id=did, limit=1))
    assert len(page["messages"]) == 1 and page["lead"]["revision"]
    assert (await call(api, headers, "read_dialog", dialog_id=did + 100))["isError"]
    assert (await call(api, headers, "edit_lead_note", dialog_id=did))["isError"]
    assert (await api.get("/api/mcp", headers=headers)).status_code == 405
    # A delegated token cannot authenticate the general admin API.
    api.cookies.clear()
    assert (await api.get("/api/dialogs", headers=headers)).status_code == 401
    async with container.factory() as s:
        row = await s.get(McpGrant, data["id"])
        assert (await s.get(Dialog, did)).unread == unread
        assert list(await s.scalars(select(Job.id))) == jobs
        assert data["token"] not in row.token_hash
        audits = list(await s.scalars(select(McpAudit)))
        assert any(a.outcome == "denied" for a in audits)
        assert all(data["token"] not in str(a.__dict__) for a in audits)


@pytest.mark.parametrize("state", ["expired", "revoked", "inactive", "demoted", "logout"])
async def test_current_authority_required_on_every_call(api, container, world, state):
    did = await chatting_world(container, world)
    data, headers = await grant(api, ["dialog:read"], did)
    if state == "logout":
        csrf = (await api.get("/api/auth/me")).json()["csrf_token"]
        await api.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    else:
        async with transaction(container.factory) as s:
            g = await s.get(McpGrant, data["id"])
            user = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == OWNER[0]))
            if state == "expired":
                g.expires_at = container.clock() - timedelta(seconds=1)
            elif state == "revoked":
                g.revoked = True
            elif state == "inactive":
                user.is_active = False
            else:
                user.role = Role.OPERATOR
    assert (await rpc(api, headers, "tools/list")).status_code == 401


async def test_lead_edit_requires_revision_scope_and_keeps_audit_private(api, container, world):
    did = await chatting_world(container, world)
    _, read = await grant(api, ["dialog:read"], did)
    initial = value(await call(api, read, "read_dialog", dialog_id=did))["lead"]
    _, edit = await grant(api, ["lead:edit"], did)
    async with container.factory() as s:
        jobs_before = list(await s.scalars(select(Job.id)))
    args = {"dialog_id": did, "field": "notes", "value": "Synthetic confidential note",
            "expected_revision": initial["revision"], "reason": "Private requested correction"}
    changed = value(await call(api, edit, "edit_lead_note", **args))
    assert changed["notes"] == args["value"] and changed["revision"] != initial["revision"]
    assert (await call(api, edit, "edit_lead_note", **args))["isError"]
    assert (await call(api, edit, "edit_lead_note", **{**args, "field": "ai_paused"}))["isError"]
    assert (await call(api, edit, "edit_lead_note", **{**args, "sql": "delete"}))["isError"]
    async with container.factory() as s:
        assert list(await s.scalars(select(Job.id))) == jobs_before
        audit = (await s.scalars(select(McpAudit).where(McpAudit.outcome == "edited"))).one()
        assert audit.before_hash == initial["revision"] and audit.after_hash == changed["revision"]
        assert args["value"] not in str(audit.__dict__) and args["reason"] not in str(audit.__dict__)


async def test_only_draft_caption_edit_no_publishing(api, container, world):
    async with transaction(container.factory) as s:
        post = ContentPost(persona_id=world.persona.id, caption="Before")
        s.add(post)
        await s.flush()
        pid = post.id
    _, read = await grant(api, ["content:read"], pid=pid)
    _, edit = await grant(api, ["content:edit"], pid=pid)
    before = value(await call(api, read, "read_post", post_id=pid))
    args = {"post_id": pid, "caption": "After", "expected_revision": before["revision"], "reason": "User request"}
    assert value(await call(api, edit, "edit_draft_caption", **args))["caption"] == "After"
    assert (await call(api, edit, "publish", post_id=pid))["isError"]
    async with transaction(container.factory) as s:
        (await s.get(ContentPost, pid)).status = ContentStatus.APPROVED
    assert (await call(api, edit, "edit_draft_caption", **args))["isError"]
    async with container.factory() as s:
        assert (await s.get(ContentPost, pid)).caption == "After"


async def test_transport_and_issuance_boundaries(api, container, world):
    did = await chatting_world(container, world)
    assert (await api.post("/api/mcp")).status_code == 401
    csrf = await login(api, *OPERATOR)
    assert (await api.post("/api/mcp/grants", headers={"X-CSRF-Token": csrf},
        json={"scopes": ["dialog:read"], "dialog_ids": [did]})).status_code == 403
    csrf = await login(api, *OWNER)
    assert (await api.post("/api/mcp/grants", json={"scopes": ["dialog:read"]})).status_code == 403
    data, headers = await grant(api, ["dialog:read"], did)
    assert (await rpc(api, {**headers, "Origin": "https://evil.invalid"}, "ping")).status_code == 403
    assert (await rpc(api, {**headers, "MCP-Protocol-Version": "bad"}, "ping")).status_code == 400
    assert (await rpc(api, {**headers, "Accept": "text/plain"}, "ping")).status_code == 406
    assert (await api.post("/api/mcp", headers={**headers, "Content-Type": "application/json"},
        content=b"x" * 32769)).status_code == 413
    csrf = (await api.get("/api/auth/me")).json()["csrf_token"]
    assert (await api.delete(f"/api/mcp/grants/{data['id']}", headers={"X-CSRF-Token": csrf})).status_code == 204
    assert (await rpc(api, headers, "ping")).status_code == 401
