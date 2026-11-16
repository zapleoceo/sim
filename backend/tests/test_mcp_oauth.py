"""Synthetic OAuth client; no production grants, external fetches or provider calls."""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from sim.adapters.db.models import McpAudit, McpAuthorization, McpGrant, TraceEvent
from sim.adapters.db.session import transaction
from sim.api.tracing import OAuthAccessFilter
from sim.services import mcp_cimd, mcp_oauth
from sim.services.auth import hash_token
from sim.services.mcp_cimd import OAuthError
from tests.conftest import OPERATOR, OWNER, login
from tests.test_calls import chatting_world
from tests.test_content_mcp import HEADERS, call, rpc, value

ISSUER = "https://sim.example"
CLIENT = "https://chatgpt.com/oauth/client.json"
REDIRECT = "https://chatgpt.com/synthetic_callback"
VERIFIER = "a" * 64


@pytest.fixture
def oauth(container, monkeypatch):
    cfg = container.settings
    cfg.public_url, cfg.mcp_oauth_enabled = ISSUER, True
    cfg.mcp_oauth_client_id, cfg.mcp_oauth_redirect_uri = CLIENT, REDIRECT

    async def fake_client(_):
        return "Synthetic ChatGPT"

    monkeypatch.setattr(mcp_cimd, "fetch_client", fake_client)
    return cfg


def params(**changes):
    return {"client_id": CLIENT, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "dialog:read lead:edit", "state": "opaque-client-state", "resource": ISSUER + "/api/mcp",
        "code_challenge": mcp_oauth.challenge(VERIFIER), "code_challenge_method": "S256", **changes}


async def intent(api, csrf, did, **changes):
    page = await api.get("/api/oauth/authorize", params=params(**changes))
    assert page.status_code == 200, page.text
    nonce = re.search(r'name="nonce" value="([^"]+)"', page.text)[1]
    return {"nonce": nonce, "csrf": csrf, "decision": "approve", "dialog_ids": str(did),
            "scope_dialog:read": "yes", "scope_lead:edit": "yes"}


async def code(api, container, world):
    did = await chatting_world(container, world)
    csrf = await login(api, *OWNER)
    form = await intent(api, csrf, did)
    result = await api.post("/api/oauth/consent", data=form, headers={"Origin": ISSUER})
    assert result.status_code == 303, result.text
    location = urlsplit(result.headers["location"])
    args = parse_qs(location.query)
    assert location.scheme + "://" + location.netloc + location.path == REDIRECT
    assert args["state"] == ["opaque-client-state"] and args["iss"] == [ISSUER]
    return args["code"][0], did


def token_form(raw_code, **changes):
    return {"grant_type": "authorization_code", "code": raw_code, "client_id": CLIENT,
        "redirect_uri": REDIRECT, "resource": ISSUER + "/api/mcp", "code_verifier": VERIFIER, **changes}


async def test_discovery_consent_exchange_read_edit_revoke(api, container, world, oauth):
    challenge = await api.post("/api/mcp")
    assert challenge.status_code == 401
    assert ISSUER + "/.well-known/oauth-protected-resource/api/mcp" in challenge.headers["www-authenticate"]
    resource = (await api.get("/.well-known/oauth-protected-resource/api/mcp")).json()
    assert resource["resource"] == ISSUER + "/api/mcp" and resource["authorization_servers"] == [ISSUER]
    metadata = (await api.get("/.well-known/oauth-authorization-server")).json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["client_id_metadata_document_supported"]
    raw, did = await code(api, container, world)
    response = await api.post("/api/oauth/token", data=token_form(raw))
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert response.json()["expires_in"] <= 900 and "refresh_token" not in response.json()
    headers = {**HEADERS, "Authorization": "Bearer " + token}
    api.cookies.clear()
    lead = value(await call(api, headers, "read_dialog", dialog_id=did))["lead"]
    assert value(await call(api, headers, "edit_lead_note", dialog_id=did, field="notes",
        value="Synthetic note", expected_revision=lead["revision"], reason="User requested"))["notes"] == "Synthetic note"
    assert (await call(api, headers, "read_dialog", dialog_id=did + 100))["isError"]
    assert (await api.get("/api/dialogs", headers=headers)).status_code == 401
    async with container.factory() as s:
        row = await s.scalar(select(McpGrant).where(McpGrant.token_hash == hash_token(token)))
        assert row.oauth_client_id == CLIENT and row.resource == resource["resource"] and row.dialog_ids == [did]
        audits = list(await s.scalars(select(McpAudit)))
        traces = list(await s.scalars(select(TraceEvent)))
        log = str([a.__dict__ for a in [*audits, *traces]])
        assert raw not in log and token not in log and VERIFIER not in log and "opaque-client-state" not in log
    assert (await api.post("/api/oauth/revoke", data={"token": token, "client_id": CLIENT})).status_code == 200
    assert (await rpc(api, headers, "ping")).status_code == 401


@pytest.mark.parametrize("change", [
    {"redirect_uri": "https://evil.invalid/"}, {"client_id": "http://127.0.0.1/client.json"},
    {"resource": ISSUER + "/api/admin"}, {"code_challenge_method": "plain"},
    {"scope": "account:edit"}, {"state": ""}, {"code_challenge": "short"},
])
async def test_invalid_authorization_never_redirects_or_fetches(api, oauth, monkeypatch, change):
    async def forbidden(_):
        raise AssertionError("must validate before fetching")
    monkeypatch.setattr(mcp_cimd, "fetch_client", forbidden)
    response = await api.get("/api/oauth/authorize", params=params(**change))
    assert response.status_code == 400 and "location" not in response.headers


async def test_explicit_consent_owner_csrf_scopes_ids_and_replay(api, container, world, oauth):
    assert "Sign in" in (await api.get("/api/oauth/authorize", params=params())).text
    await login(api, *OPERATOR)
    assert (await api.get("/api/oauth/authorize", params=params())).status_code == 403
    csrf = await login(api, *OWNER)
    did = await chatting_world(container, world)
    form = await intent(api, csrf, did)
    for headers, update, expected in [({}, {}, 403), ({"Origin": ISSUER}, {"csrf": "wrong"}, 403),
            ({"Origin": ISSUER}, {"dialog_ids": ""}, 400),
            ({"Origin": ISSUER}, {"scope_content:edit": "yes"}, 400),
            ({"Origin": ISSUER}, {"dialog_ids": str(did + 100)}, 400)]:
        assert (await api.post("/api/oauth/consent", data={**form, **update}, headers=headers)).status_code == expected
    denied = await api.post("/api/oauth/consent", data={**form, "decision": "deny"}, headers={"Origin": ISSUER})
    query = parse_qs(urlsplit(denied.headers["location"]).query)
    assert query == {"error": ["access_denied"], "state": ["opaque-client-state"], "iss": [ISSUER]}
    assert (await api.post("/api/oauth/consent", data=form, headers={"Origin": ISSUER})).status_code == 400
    async with container.factory() as s:
        assert not list(await s.scalars(select(McpGrant)))


@pytest.mark.parametrize("change", [{"code_verifier": "b" * 64}, {"code_verifier": "x"},
    {"resource": "https://other.example/api/mcp"}, {"redirect_uri": REDIRECT + "/other"},
    {"client_id": "https://other.example/client.json"}, {"grant_type": "refresh_token"}])
async def test_code_bindings_fail_without_consuming(api, container, world, oauth, change):
    raw, _ = await code(api, container, world)
    assert (await api.post("/api/oauth/token", data=token_form(raw, **change))).status_code == 400
    assert (await api.post("/api/oauth/token", data=token_form(raw))).status_code == 200


async def test_valid_replay_revokes_but_wrong_verifier_cannot_revoke(api, container, world, oauth):
    raw, _ = await code(api, container, world)
    first = (await api.post("/api/oauth/token", data=token_form(raw))).json()
    headers = {**HEADERS, "Authorization": "Bearer " + first["access_token"]}
    assert (await api.post("/api/oauth/token", data=token_form(raw, code_verifier="b" * 64))).status_code == 400
    assert (await rpc(api, headers, "ping")).status_code == 200
    assert (await api.post("/api/oauth/token", data=token_form(raw))).status_code == 400
    assert (await rpc(api, headers, "ping")).status_code == 401


@pytest.mark.parametrize("change", ["expired", "issuer", "resource", "logout", "disabled"])
async def test_expiry_and_current_authority(api, container, world, oauth, change):
    raw, _ = await code(api, container, world)
    if change == "expired":
        async with transaction(container.factory) as s:
            row = await s.scalar(select(McpAuthorization))
            row.expires_at = container.clock() - timedelta(seconds=1)
        assert (await api.post("/api/oauth/token", data=token_form(raw))).status_code == 400
        return
    first = (await api.post("/api/oauth/token", data=token_form(raw))).json()
    if change == "logout":
        csrf = (await api.get("/api/auth/me")).json()["csrf_token"]
        await api.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    elif change == "disabled":
        oauth.mcp_oauth_enabled = False
    else:
        async with transaction(container.factory) as s:
            row = await s.scalar(select(McpGrant))
            setattr(row, change, "https://wrong.example")
    assert (await rpc(api, {**HEADERS, "Authorization": "Bearer " + first["access_token"]}, "ping")).status_code == 401


async def test_duplicate_fields_and_default_disabled(api, container):
    assert (await api.get("/.well-known/oauth-authorization-server")).status_code == 503
    assert (await api.post("/api/oauth/token")).status_code == 503
    with pytest.raises(OAuthError):
        mcp_cimd.configuration(container.settings)


async def test_ambiguous_params_and_oversized_forms_rejected(api, oauth):
    response = await api.get("/api/oauth/authorize", params=[*params().items(), ("client_id", CLIENT)])
    assert response.status_code == 400
    for body in ("client_id=a&client_id=b", "code=" + "x" * 8192):
        response = await api.post("/api/oauth/token", content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert response.status_code == 400


def test_uvicorn_oauth_query_is_not_logged():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
        ("client", "GET", "/api/oauth/authorize?state=secret&code_challenge=private", "1.1", 200), None)
    assert OAuthAccessFilter().filter(record)
    assert "secret" not in record.getMessage() and "private" not in record.getMessage()


def test_cimd_exact_identity_and_plural_public_client():
    doc = {"client_id": CLIENT, "client_name": "ChatGPT", "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "private_key_jwt", "token_endpoint_auth_methods_supported": ["private_key_jwt", "none"]}
    assert mcp_cimd.validate_document(doc, CLIENT, REDIRECT) == "ChatGPT"
    for key, val in [("client_id", CLIENT + "/x"), ("redirect_uris", ["https://chatgpt.com/*"]),
            ("token_endpoint_auth_methods_supported", ["private_key_jwt"]), ("grant_types", "authorization_code")]:
        with pytest.raises(OAuthError):
            mcp_cimd.validate_document({**doc, key: val}, CLIENT, REDIRECT)


async def test_cimd_pins_ip_tls_host_no_redirects(container, monkeypatch, respx_mock):
    cfg = container.settings
    cfg.public_url, cfg.mcp_oauth_enabled = ISSUER, True
    cfg.mcp_oauth_client_id, cfg.mcp_oauth_redirect_uri = CLIENT, REDIRECT

    async def public(_):
        return ["93.184.216.34"]

    monkeypatch.setattr(mcp_cimd, "public_addresses", public)
    doc = {"client_id": CLIENT, "client_name": "ChatGPT", "redirect_uris": [REDIRECT]}
    route = respx_mock.get("https://93.184.216.34/oauth/client.json")

    def respond(request):
        assert request.headers["host"] == "chatgpt.com" and request.extensions["sni_hostname"] == "chatgpt.com"
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(json.dumps(doc).encode()))

    route.mock(side_effect=respond)
    assert await mcp_cimd.fetch_client(cfg) == "ChatGPT"
    route.respond(302, headers={"Location": "http://127.0.0.1/secret"})
    with pytest.raises(OAuthError):
        await mcp_cimd.fetch_client(cfg)
    route.mock(return_value=httpx.Response(200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(b"x" * 16385)))
    with pytest.raises(OAuthError):
        await mcp_cimd.fetch_client(cfg)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1", "::ffff:127.0.0.1"])
async def test_cimd_rejects_nonpublic_dns(monkeypatch, ip):
    import asyncio

    async def addresses(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", (ip, 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    with pytest.raises(OAuthError):
        await mcp_cimd.public_addresses("chatgpt.com")
