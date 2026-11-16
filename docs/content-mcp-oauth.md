# ChatGPT OAuth compatibility (disabled until a separately approved release)

This adds an OAuth authorization-code adapter for the existing bounded content MCP, not
a new admin API. It does not connect ChatGPT, issue production grants, add permanent
credentials, or authorize content changes. The draft must receive security review and
explicit release approval before merge/deploy. Existing manual grants remain supported.

## Protocol and trust boundaries

Discovery: `/.well-known/oauth-protected-resource` and the path-specific
`/.well-known/oauth-protected-resource/api/mcp`; authorization metadata at
`/.well-known/oauth-authorization-server`. The issuer is the configured HTTPS public origin,
never the incoming Host header. Resource/audience is exactly `<issuer>/api/mcp`. A 401 MCP
challenge advertises resource metadata when OAuth is enabled and correctly configured.

Only authorization code, PKCE S256, public-client authentication `none`, and one explicitly
configured ChatGPT CIMD/redirect pair are supported. There is no implicit flow, password
grant, refresh token, DCR, wildcard callback or arbitrary client registration. All of client,
redirect, resource and PKCE are checked again on exchange. `state` is mandatory, opaque,
preserved exactly; the client must validate it. Redirects carry `iss` on success and denial.
Invalid authorization requests stay local rather than redirect to an untrusted destination.

CIMD is fetched only after exact client/redirect validation and existing owner login.
The configured URL must be an HTTPS chatgpt.com `/oauth/client.json` or
`/oauth/<callback-id>/client.json`. Every DNS answer must be globally routable; the request
connects to a validated IP with the original TLS SNI/certificate name and Host header.
Environment proxies and redirects are disabled; timeout 8 seconds, uncompressed JSON maximum
16 KiB. The document must match client identity, the configured exact redirect, code flow,
and support `none`. The plural methods list takes precedence over a legacy private-key
preference. No caller-provided URLs, JWKS, logo or policy URLs are fetched.

Existing SIM owner authentication is the only login. If necessary the consent screen opens
the ordinary login in another tab; return and click Continue. No new identity or security
role is created. Consent binds a random server nonce to that owner session, the complete
OAuth request and a five-minute deadline. Approval requires same-origin POST, the session
CSRF token, explicitly selected requested scopes, and exact existing entity IDs (max 20 each).
No checkbox is preselected. Dialog scopes require dialog IDs; content scopes require post
IDs. Read and edit may be combined only through this explicit OAuth consent (the manual
grant endpoint still requires separate read/edit grants). Omitted OAuth scope requests only
`dialog:read`; scopes cannot be elevated during consent or exchange.

Codes expire in 60 seconds and are one-use under a database row lock. Tokens expire within
15 minutes and never outlive the owner session. Only hashes of codes, nonces and tokens are
stored. Replay with correct bindings and verifier revokes the previously issued token;
invalid verifiers cannot revoke it. Concurrent exchanges yield at most one token, which is
revoked when the second valid exchange detects replay. Retry a lost exchange by authorizing
again. `/api/oauth/revoke` supports token possession plus exact public client ID; existing
owner/CSRF grant revocation remains available. Every MCP call checks expiry, revocation,
parent session, active owner role, issuer/resource/client and current OAuth enablement.
Logout, role removal, config changes or disabling OAuth invalidate delegated access.

MCP tools and explicit entity checks are unchanged: bounded reads, lead notes/next step and
draft captions only, revision conflicts and metadata-only audit. No sends, account/security
management, deletion, publishing, arbitrary fetch, shell or SQL. Consent and exchange audit
never copy tokens, PKCE, state, IDs lists or private text. Request tracing and Uvicorn access
logging suppress OAuth queries. HTML is escaped, no-store, unframeable, no-referrer and has
a restrictive CSP. Public requests have bounded input and a bounded per-process/IP limiter;
the reverse proxy remains responsible for distributed rate limiting.

## Activation gates (not performed by this PR)

1. Security review, exact-head required CI, then explicit owner approval to merge/deploy
   this new access mechanism. Standard backup, stop-worker, migration 0029 and readiness
   gates remain mandatory. The default is `SIM_MCP_OAUTH_ENABLED=false`.
2. Review the deployed proxy and TLS path before enabling. The repository's historical
   nginx sample documents Cloudflare Flexible HTTP-to-origin; that is **not sufficient**
   for OAuth credentials. Require verified HTTPS to the origin (or an equivalently approved
   authenticated encrypted tunnel), and suppress OAuth query strings in proxy/CDN access
   and error logs. This PR does not change production TLS/security settings. Confirm cookie
   Secure, trusted proxies and public HTTPS origin. Do not enable until these are verified.
3. In ChatGPT Plugins, `+` → Add custom MCP server → OAuth, use the canonical HTTPS
   `/api/mcp` endpoint. Obtain the actual client metadata URL and exact redirect shown for
   that app. Do not guess a callback from an example. Agree target client and bounded
   scopes/IDs. Configure `SIM_MCP_OAUTH_CLIENT_ID`, `SIM_MCP_OAUTH_REDIRECT_URI`, public URL,
   then separately approve enablement/connection. No shared client secret is needed.
4. Create as a plugin → Install. The owner signs into SIM and explicitly consents to the
   selected IDs/operations. A short-lived grant is created only by that approval. A successful
   synthetic client test is not proof of real ChatGPT interoperability: inspect discovery,
   consent, tools and revocation in the actual integration after authorized activation.
   Read first; edit only on a concrete user request. No real sends or paid probes are needed.

## CI coverage and limitations

Offline HTTP tests use synthetic content and a mocked CIMD transport: discovery/challenge,
owner login and explicit consent, PKCE and all bindings, scopes/IDs/CSRF/origin, denied consent,
replay/revocation/expiry/logout/disabled state, audience checks, duplicate/oversized requests,
private DNS/redirect/oversize CIMD, pinning/TLS hostname and audit redaction. Temporary
PostgreSQL applies migration 0029 and races code redemption; no production DB is used.
Existing content MCP tests cover revision races, edit rollback, role demotion and no sends.
No local checks, real grants, provider calls or actual ChatGPT installation are performed.

References reviewed directly:
- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/api/docs/guides/custom-mcp-server
- https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- https://www.encode.io/httpcore/extensions/ (SNI pinning)
