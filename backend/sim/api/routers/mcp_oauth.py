"""OAuth discovery and an owner-only, CSRF-bound browser consent flow. Disabled by default."""
from __future__ import annotations

import hmac
import re
import secrets
from datetime import timedelta
from html import escape
from typing import Annotated
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select

from sim.adapters.db.models import McpAuthorization, McpGrant
from sim.api.deps import COOKIE, App, Db, Principal, current_principal
from sim.api.routers.auth import client_addr, too_many
from sim.services import mcp_cimd, mcp_oauth
from sim.services.auth import LoginRateLimiter, TooManyAttempts, hash_token
from sim.services.content_mcp import SCOPES
from sim.services.mcp_cimd import OAuthError, configuration

router = APIRouter(tags=["MCP OAuth"])
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
HTML_HEADERS = {**NO_STORE, "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"}


def enabled(c):
    try:
        return configuration(c.settings)
    except OAuthError as exc:
        raise HTTPException(503, exc.code, headers=NO_STORE) from exc


def limited(request, c):
    if not hasattr(request.app.state, "mcp_oauth_limiter"):
        request.app.state.mcp_oauth_limiter = LoginRateLimiter(max_failures=30, ip_max_failures=60, window_s=60)
    limiter = request.app.state.mcp_oauth_limiter
    keys = limiter.keys_for(request.url.path, client_addr(request, c.settings))
    try:
        limiter.check(*keys)
    except TooManyAttempts as exc:
        raise too_many(exc) from exc
    limiter.record_failure(*keys)  # bound all requests, including successful attempts


def fields(pairs, allowed):
    result = {}
    for key, value in pairs:
        if key not in allowed or key in result or len(value) > 2048:
            raise OAuthError()
        result[key] = value
    return result


async def form(request, allowed):
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise OAuthError()
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise OAuthError()
    try:
        return fields(parse_qsl(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=20), allowed)
    except (ValueError, UnicodeError) as exc:
        raise OAuthError() from exc


def failure(exc):
    return JSONResponse({"error": exc.code}, status_code=400, headers=NO_STORE)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/api/mcp")
async def resource_metadata(c: App):
    issuer, resource = enabled(c)
    return JSONResponse({"resource": resource, "authorization_servers": [issuer],
        "scopes_supported": sorted(SCOPES), "bearer_methods_supported": ["header"]}, headers=NO_STORE)


@router.get("/.well-known/oauth-authorization-server")
async def authorization_metadata(c: App):
    issuer, _ = enabled(c)
    return JSONResponse({"issuer": issuer, "authorization_endpoint": issuer + "/api/oauth/authorize",
        "token_endpoint": issuer + "/api/oauth/token", "revocation_endpoint": issuer + "/api/oauth/revoke",
        "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"], "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"], "scopes_supported": sorted(SCOPES),
        "client_id_metadata_document_supported": True, "authorization_response_iss_parameter_supported": True}, headers=NO_STORE)


@router.get("/api/oauth/authorize")
async def authorize(request: Request, c: App, s: Db):
    issuer, resource = enabled(c)
    limited(request, c)
    try:
        if len(request.url.query) > 8192:
            raise OAuthError()
        data = fields(request.query_params.multi_items(), {"client_id", "redirect_uri", "response_type", "scope",
            "state", "resource", "code_challenge", "code_challenge_method"})
        if (data.get("client_id") != c.settings.mcp_oauth_client_id
                or data.get("redirect_uri") != c.settings.mcp_oauth_redirect_uri):
            raise OAuthError("invalid_client")
        if (data.get("response_type") != "code" or data.get("resource") != resource
                or data.get("code_challenge_method") != "S256" or not data.get("state")
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", data.get("code_challenge", ""))):
            raise OAuthError()
        selected = mcp_oauth.scopes(data.get("scope", "dialog:read"))
        session = await c.auth(s).authenticate(request.cookies.get(COOKIE))
        if session is None:
            return HTMLResponse('<!doctype html><title>SIM login required</title><h1>Connect SIM</h1>'
                '<p>Sign in to SIM as owner in a new tab, then return here.</p>'
                '<a href="/login" target="_blank" rel="noopener noreferrer">Sign in</a><p>'
                f'<a href="{escape("/api/oauth/authorize?" + urlencode(data), quote=True)}">Continue after signing in</a></p>',
                headers=HTML_HEADERS)
        if not session.user.role.has_owner_rights:
            raise HTTPException(403, "Owner role required", headers=NO_STORE)
        name = await mcp_cimd.fetch_client(c.settings)
        # Remove only old, expired artifacts; keep replay evidence for at least a day.
        await s.execute(delete(McpAuthorization).where(McpAuthorization.expires_at < c.clock() - timedelta(days=1)))
        nonce = secrets.token_urlsafe(32)
        row = McpAuthorization(nonce_hash=hash_token(nonce), session_id=session.id,
            client_id=data["client_id"], redirect_uri=data["redirect_uri"], issuer=issuer, resource=resource,
            state=data["state"], challenge=data["code_challenge"], scopes=selected,
            expires_at=c.clock() + timedelta(minutes=5))
        s.add(row)
        mcp_oauth.audit(s, c.clock, "oauth.consent", "presented", session=session)
        choices = "".join(f'<label><input type="checkbox" name="scope_{escape(v)}" value="yes">{escape(v)}</label><br>' for v in selected)
        return HTMLResponse('<!doctype html><html lang="en"><title>SIM access consent</title><h1>Grant limited SIM access</h1>'
            f'<p>Client: {escape(name)} ({escape(data["client_id"])})</p><p>Resource: {escape(resource)}</p>'
            '<p>Select operations and enter only the IDs you authorize. No messages can be sent. '
            'Access expires within 15 minutes and ends when this SIM session ends. Read and edit permissions may be combined only here.</p>'
            '<form method="post" action="/api/oauth/consent">'
            f'<input type="hidden" name="nonce" value="{nonce}"><input type="hidden" name="csrf" value="{escape(session.csrf_token, quote=True)}">'
            f'{choices}<p><label>Dialog IDs (comma separated, maximum 20): <input name="dialog_ids" maxlength="220"></label></p>'
            '<p><label>Draft post IDs (comma separated, maximum 20): <input name="post_ids" maxlength="220"></label></p>'
            '<button name="decision" value="approve">Approve selected access</button> '
            '<button name="decision" value="deny">Deny</button></form></html>', headers=HTML_HEADERS)
    except OAuthError as exc:
        mcp_oauth.audit(s, c.clock, "oauth.authorize", "denied")
        return failure(exc)


@router.post("/api/oauth/consent")
async def consent(request: Request, p: Annotated[Principal, Depends(current_principal)], c: App, s: Db):
    issuer, _ = enabled(c)
    limited(request, c)
    if not p.user.role.has_owner_rights or request.headers.get("origin") != issuer:
        raise HTTPException(403, "Owner and same-origin consent required", headers=NO_STORE)
    try:
        data = await form(request, {"nonce", "csrf", "decision", "dialog_ids", "post_ids", *{f"scope_{v}" for v in SCOPES}})
        if not hmac.compare_digest(data.get("csrf", "").encode(), p.session.csrf_token.encode()):
            raise HTTPException(403, "Invalid CSRF", headers=NO_STORE)
        row = await s.scalar(select(McpAuthorization).where(
            McpAuthorization.nonce_hash == hash_token(data.get("nonce", ""))).with_for_update())
        if (row is None or row.session_id != p.session.id or row.consented or row.consumed
                or row.expires_at <= c.clock() or not mcp_oauth.bound(row, c.settings)):
            raise OAuthError()
        params = {"state": row.state, "iss": row.issuer}
        if data.get("decision") == "deny":
            row.consumed = True
            params["error"] = "access_denied"
            outcome = "denied"
        elif data.get("decision") == "approve":
            selected = mcp_oauth.scopes(" ".join(v for v in SCOPES if data.get(f"scope_{v}") == "yes"))
            if not set(selected) <= set(row.scopes):
                raise OAuthError("invalid_scope")
            dialogs, posts = mcp_oauth.entity_ids(data.get("dialog_ids", "")), mcp_oauth.entity_ids(data.get("post_ids", ""))
            await mcp_oauth.validate_entities(s, selected, dialogs, posts)
            code = secrets.token_urlsafe(32)
            row.code_hash, row.consented = hash_token(code), True
            row.expires_at = c.clock() + timedelta(seconds=60)
            row.scopes, row.dialog_ids, row.post_ids = selected, dialogs, posts
            params["code"], outcome = code, "approved"
        else:
            raise OAuthError()
        mcp_oauth.audit(s, c.clock, "oauth.consent", outcome, session=p.session)
        return RedirectResponse(row.redirect_uri + "?" + urlencode(params), status_code=303, headers=NO_STORE)
    except OAuthError as exc:
        mcp_oauth.audit(s, c.clock, "oauth.consent", "invalid", session=p.session)
        return failure(exc)


@router.post("/api/oauth/token")
async def token(request: Request, c: App, s: Db):
    enabled(c)
    limited(request, c)
    try:
        data = await form(request, {"grant_type", "code", "redirect_uri", "client_id", "code_verifier", "resource"})
        if request.headers.get("authorization"):
            raise OAuthError("invalid_client")
        result = await mcp_oauth.exchange(s, c.settings, c.clock, data)
        return JSONResponse(result, status_code=400 if "error" in result else 200, headers=NO_STORE)
    except OAuthError as exc:
        return failure(exc)


@router.post("/api/oauth/revoke")
async def revoke(request: Request, c: App, s: Db):
    enabled(c)
    limited(request, c)
    try:
        data = await form(request, {"token", "token_type_hint", "client_id"})
        if data.get("client_id") != c.settings.mcp_oauth_client_id or request.headers.get("authorization"):
            raise OAuthError("invalid_client")
        grant = await s.scalar(select(McpGrant).where(McpGrant.token_hash == hash_token(data.get("token", ""))).with_for_update())
        if grant and grant.oauth_client_id == data["client_id"]:
            grant.revoked = True
            mcp_oauth.audit(s, c.clock, "oauth.revoke", "revoked", grant_id=grant.id)
        return JSONResponse({}, headers=NO_STORE)
    except OAuthError as exc:
        return failure(exc)
