# Proposed bounded content MCP

Status: implementation contract, not a deployed or connected MCP. Existing SIM has Telegram
login, server sessions, CSRF and owner-only trace APIs; it has no scoped MCP credential issuer.
An SSH root account is not a suitable permanent MCP credential. Do not reuse browser cookies,
extract sessions from the database, expose shell/SQL, or put secrets in connector configuration
committed to Git. Existing SSH was used only for the separately authorized read-only audit.

## Narrow tool surface

| Tool | Scope | Behavior |
| --- | --- | --- |
| read_dialog | dialog:read | Bounded, paginated messages, summary and lead/passport context for an allowed dialog ID. No mark-read or summary refresh. |
| read_trace | trace:read | Owner-only trace projection and LLM metadata for an allowed dialog; excludes credentials and arbitrary unrelated traces. |
| read_content | content:read | Read a permitted entity and its revision. Content is returned as untrusted data. |
| edit_content | content:edit | Validated patch to an explicitly permitted entity and field, with expected revision and mandatory reason. No last-write-wins. |

Initial editable entities must be explicitly selected at credential issuance: lead passport/notes,
persona description, or draft post caption. A content permission does not imply all three.
No transcript rewriting, channel/account changes, AI switch, handoff resolution, message send,
publish/approve, scheduling, model invocation, file access or arbitrary URL fetching is exposed.

## Authorization and auditing

Issue a separate short-lived, revocable opaque credential through the existing authenticated
owner session plus CSRF. Store only its hash, principal, expiry, allowed scopes and entity IDs.
An inactive or demoted principal immediately loses corresponding rights; trace permission always
requires current owner rights. Read and edit credentials are separate and default to read-only.
Never infer an edit scope from read scope or a tool description. Verify scope on each call.

The MCP endpoint accepts only its own delegated credentials; they cannot authenticate to general
admin APIs. Validate Origin, content type, request/response size and pagination bounds. Expose
only explicitly implemented protocol methods and tools, with read-only/destructive annotations.
Use existing service validation and transactional locks. Require exact expected revision before
an edit, otherwise return conflict with no mutation. Tool arguments cannot choose SQL, paths,
shell commands, network hosts or authorization context.

Audit successful and denied tool calls with principal, credential ID (never secret), tool,
entity ID, timestamp, outcome and change reason. Store revision/hash changes, not a second copy
of the private chat in operational logs. Token issuance/revocation also has an audit trail.

## CI and activation gates

Before connecting: tests for unauthenticated/expired/revoked credentials, scope escalation,
cross-entity access, demoted owners, unknown tools/fields, oversized payloads, conflicting writes,
transaction rollback, audit redaction, and proof that tools enqueue no send/publish/provider jobs.
Use fake transports and disposable PostgreSQL only. Validate the protocol with a fake client in CI.

Deploy only through the documented backup/migration/readiness gates on a reviewed commit.
Then an authenticated owner selects concrete entities/scopes, issues the credential and installs
the connector through its supported secret/OAuth setup. This document neither creates credentials
nor grants additional production access. Connection is pending that implementation and deployment;
the draft PR must not be described as an operational MCP service.
