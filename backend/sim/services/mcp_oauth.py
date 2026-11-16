"""Narrow OAuth grants: existing owner authority, explicit IDs, single-use S256 codes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlalchemy import select

from sim.adapters.db.models import (
    AuthSession,
    ContentPost,
    Dialog,
    McpAudit,
    McpAuthorization,
    McpGrant,
)
from sim.services.auth import hash_token
from sim.services.content_mcp import SCOPES
from sim.services.mcp_cimd import OAuthError, configuration


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def scopes(value: str) -> list[str]:
    values = value.split()
    if not values or len(values) > len(SCOPES) or not set(values) <= SCOPES:
        raise OAuthError("invalid_scope")
    return sorted(set(values))


def entity_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    parts = value.split(",")
    if len(parts) > 20 or any(not re.fullmatch(r"[1-9][0-9]{0,9}", p.strip()) for p in parts):
        raise OAuthError()
    return sorted({int(p) for p in parts})


async def validate_entities(s, selected: list[str], dialogs: list[int], posts: list[int]) -> None:
    needs_dialog = bool(set(selected) & {"dialog:read", "trace:read", "lead:edit"})
    needs_post = bool(set(selected) & {"content:read", "content:edit"})
    if bool(dialogs) != needs_dialog or bool(posts) != needs_post:
        raise OAuthError()
    for ids, model in ((dialogs, Dialog), (posts, ContentPost)):
        if set(await s.scalars(select(model.id).where(model.id.in_(ids)))) != set(ids):
            raise OAuthError()


def audit(s, clock, action, outcome, *, session=None, grant_id=None):
    s.add(McpAudit(action=action, outcome=outcome, created_at=clock(),
                   user_id=session.user_id if session else None, grant_id=grant_id))


def bound(row, cfg) -> bool:
    issuer, resource = configuration(cfg)
    return (row.issuer == issuer and row.resource == resource
            and row.client_id == cfg.mcp_oauth_client_id and row.redirect_uri == cfg.mcp_oauth_redirect_uri)


async def exchange(s, cfg, clock, data: dict[str, str]) -> dict:
    """Return errors (not rollback exceptions): valid code replay revokes its issued grant."""
    configuration(cfg)
    if data.get("grant_type") != "authorization_code":
        return {"error": "unsupported_grant_type"}
    if data.get("client_id") != cfg.mcp_oauth_client_id:
        return {"error": "invalid_client"}
    row = await s.scalar(select(McpAuthorization).where(
        McpAuthorization.code_hash == hash_token(data.get("code", ""))).with_for_update())
    verifier = data.get("code_verifier", "")
    if (row is None or not bound(row, cfg) or data.get("redirect_uri") != row.redirect_uri
            or data.get("resource") != row.resource
            or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
            or not hmac.compare_digest(challenge(verifier), row.challenge)):
        audit(s, clock, "oauth.exchange", "denied")
        return {"error": "invalid_grant"}
    if row.consumed:
        grant = await s.get(McpGrant, row.grant_id, with_for_update=True) if row.grant_id else None
        if grant:
            grant.revoked = True
        audit(s, clock, "oauth.exchange", "replay", grant_id=row.grant_id)
        return {"error": "invalid_grant"}
    session = await s.get(AuthSession, row.session_id)
    if (not row.consented or row.expires_at <= clock() or session is None
            or session.expires_at <= clock() or not session.user.is_active or not session.user.role.has_owner_rights):
        audit(s, clock, "oauth.exchange", "expired_or_authority", session=session)
        return {"error": "invalid_grant"}
    token = "sim_mcp_" + secrets.token_urlsafe(32)
    grant = McpGrant(session_id=session.id, token_hash=hash_token(token), scopes=row.scopes,
        dialog_ids=row.dialog_ids, post_ids=row.post_ids, oauth_client_id=row.client_id,
        issuer=row.issuer, resource=row.resource, expires_at=min(session.expires_at, clock() + timedelta(minutes=15)))
    s.add(grant)
    await s.flush()
    row.consumed, row.grant_id = True, grant.id
    audit(s, clock, "oauth.exchange", "issued", session=session, grant_id=grant.id)
    return {"access_token": token, "token_type": "Bearer", "expires_in": int((grant.expires_at - clock()).total_seconds()),
            "scope": " ".join(grant.scopes)}
