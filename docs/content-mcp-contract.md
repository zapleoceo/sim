# Bounded content MCP

Implementation: `/api/mcp`, stateless Streamable HTTP JSON, protocol `2025-06-18`.
Supports initialize, initialized notification, ping, tools/list and tools/call. GET returns
405 (no unsolicited SSE). Reference: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
No production grant has been issued and no external connector has been connected by this change.

## Tools and scope

| Tool | Scope | Allowed action |
| --- | --- | --- |
| read_dialog | dialog:read | One permitted dialog, paginated messages (max 50), passport and lead notes; no mark-read or refresh. |
| read_checks | trace:read | LLM metadata for one permitted dialog; no raw prompts, errors or unrelated traces. Guard verdicts are in read_dialog. |
| read_post | content:read | One permitted post caption, status and revision. |
| edit_lead_note | lead:edit | Only notes or next_step on the lead belonging to a permitted dialog. |
| edit_draft_caption | content:edit | Only caption of a draft with no publication attempt. |

Every edit requires the current content revision and a non-empty change reason. Read again
after a revision conflict. Row locks serialize concurrent edits. No transcript rewriting,
passport replacement, persona prompts, account changes, permissions, sends, model calls,
publish/approve, scheduling, deletion, shell/SQL, files or arbitrary URLs are exposed.
Content is untrusted data; tool access does not authorize arbitrary edits. Edit only for a
concrete user-requested change.

## Authorization and activation

An authenticated owner can POST `/api/mcp/grants` with the existing session and CSRF header.
Body: `scopes`, explicit `dialog_ids` and/or `post_ids` (max 20 each), `ttl_seconds` (60-3600,
default 900). Read and edit grants must be separate. The response returns a token once with
Cache-Control no-store; only SHA-256 is stored. No refresh token or permanent credential exists.
An MCP bearer cannot authenticate the general admin API. MCP does not accept browser cookies
as delegated credentials. Each request rechecks parent session, expiry, revocation, active
user and owner role; logout invalidates delegation. Revoke with authenticated owner + CSRF:
DELETE `/api/mcp/grants/{id}`. Issuance/revocation are management API operations, not tools.

After reviewed CI and standard deployment gates, agree the target client, exact IDs/scopes
and TTL. Then the owner issues a grant through the authenticated API and enters it into the
client secret store. Do not extract cookies, copy root/SSH credentials, print secrets or silently
issue a grant. The client must support a manually configured Authorization Bearer header;
OAuth-only clients use the separately reviewed, default-disabled adapter described in
[content-mcp-oauth.md](content-mcp-oauth.md). It requires explicit owner consent and separate
release/activation approval. No broader SIM rights are needed.

Client headers: Authorization Bearer token, Content-Type application/json,
Accept application/json, text/event-stream, MCP-Protocol-Version 2025-06-18.
Origin, when present, must exactly match the configured public URL origin. Browser cross-origin
access is not enabled. Requests are capped at 32 KiB; reads at 256 KiB. Use smaller pages for
large messages. There is no bulk export or enumeration of all dialogs/posts.

## Audit, migration and tests

Migration 0028 adds mcp_grant and mcp_audit. Audit records principal/grant IDs, fixed action,
entity ID, timestamp, outcome and before/after/reason hashes; no raw arguments, tokens or copied
chat text. Rejected authentication/tool calls are recorded. Edits and audit commit together;
invalid/conflicting calls leave content untouched.

CI uses an offline HTTP client, synthetic data and fake transports. Coverage includes scopes,
entity boundaries, unknown fields/tools, revision conflicts, draft-only edits, no queued sends,
owner/CSRF issuance, expiry/revoke/logout/demotion, protocol/media/Origin/size rejection and audit
redaction. The temporary PostgreSQL gate applies migrations. Deployment retains fresh backup,
old worker stop, migrations before new code and readiness smoke. Code readiness does not mean
an external connector is activated.

The disposable PostgreSQL gate also races two caption editors against one revision: exactly one wins. A forced transaction rollback verifies that both content and audit roll back together.
