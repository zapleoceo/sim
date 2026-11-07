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

A permanent independently committed intent now fences each post before the upload adapter is
called. Exceptions, missing IDs, cancellation, process loss, caller rollback and repeated jobs
cannot authorize another upload for that post. A separately committed external receipt can
restore local publication status after caller rollback when the payload/account fingerprint
still matches. Editing and owner reapproval of attempted posts are blocked.

Owners can inspect `/api/content/{id}/publication` without changing state. Unknown outcomes
require actual-account inspection; there is no reset/retry endpoint or manual-resolution action
in this patch. FAILED does not prove nothing was published. Session refresh, changed media or
changed caption can prevent receipt attribution, never permit a new upload.

See [Publication intents and reconciliation](publication-intents.md) for migration 0006,
retained history, downgrade restrictions, fingerprint details and recovery limits. This is
protection against repeated adapter invocation, not external exactly-once delivery. PostgreSQL
locking and live Instagram remain untested; activation must still be separately authorized.

## Offline verification

Fake-port tests cover both gates, success, invalid media, account/persona mismatch and inactivity,
missing capabilities, future schedules, stale ORM refresh, null response IDs, sanitized exceptions
and no repeat after committed failure. Durable-intent tests additionally cover caller rollback,
commit failures, cancellation, concurrent SQLite claims, retained receipts and migration rollback. All checks use offline_run.py with network sockets blocked.
No real publication, paid API call or production access was used.
