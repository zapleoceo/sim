# Instagram content publishing: disabled-by-default pilot

This integration connects ContentService to Instagram's existing single-photo capability.
It does not enable publication anywhere. Both SIM_IG_CONTENT_ENABLED (default false) and
SIM_IG_CONTENT_POST_IDS (default empty JSON array) are required. Deploying alone or enabling
only the boolean cannot release scheduled backlog. The explicit post-ID allowlist is an
operational gate, not a replacement for owner approval. Draft, failed and published posts
are not automatically sent; future schedules are not sent early. Disabled due jobs continue
to fail visibly, as before.

## Separately authorized activation

Review the specific post, caption, persona, account and media with the owner first. Require
an active persona and an explicit, active, nonarchived Instagram channel belonging to it,
with a saved session. No account is guessed, even when only one is present. For a supervised
pilot only, configure SIM_IG_CONTENT_ENABLED=true and SIM_IG_CONTENT_POST_IDS as the JSON
array of reviewed IDs, e.g. `[123]`. No such configuration is installed by this patch. Remove
completed IDs and disable the switch to stop new attempts. This patch authorizes no production
access, publications or activation.

Accepts exactly one trusted local JPEG reference `/api/media/<safe-generated-name>.jpg`
(or `.jpeg`) under SIM_MEDIA_DIR. There is no new upload API or complete admin publishing UX.
Use existing trusted local media. Reels, video, stories, carousels, arbitrary paths, remote
URLs, queries/fragments and symlinks are rejected before connector I/O. The JPEG header check
is only preflight, not full image validation. Protect the media directory from malicious or
concurrent filesystem changes; these path checks do not defeat a privileged local attacker
replacing a file during upload.

## Uncertain outcomes and recovery

Caught upload exceptions and missing media IDs become FAILED with a sanitized uncertain-outcome
message. There is no automatic retry after that failure is committed. Check the actual account
before any manual reapproval or reschedule. FAILED does not prove that nothing was published.
Existing owner reapproval remains possible; this patch adds no durable external ID, idempotency
key, or reconciliation workflow. Adapter error text and session details are not exposed.

PostgreSQL row locks plus ORM refresh serialize publishers and update/approval checks. SQLite
ignores row locks: use it only for local single-worker testing. The offline suite checks stale
ORM refresh but does not prove PostgreSQL concurrency. The remote request remains inside the
DB transaction. A crash, cancellation, or commit failure after remote success may roll back
local state and allow a later duplicate. Exactly-once and crash-safe retry are NOT guaranteed.
Production activation requires durable intent/reconciliation or an operator-controlled recovery
process preventing automatic replay after uncertain transaction outcomes. Pilot use must be
supervised and separately authorized.

## Offline verification

Fake-port tests cover both gates, success, invalid media, account/persona mismatch and inactivity,
missing capabilities, future schedules, stale ORM refresh, null response IDs, sanitized exceptions
and no repeat after committed failure. All checks use offline_run.py with network sockets blocked.
No real publication, paid API call or production access was used.
