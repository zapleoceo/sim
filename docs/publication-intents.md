# Instagram publication intent and reconciliation

Publishing remains disabled by default and requires explicit post IDs. This patch does not activate publishing, migrate production, or make external calls during validation.

## Guarantee and boundary

Before calling the Instagram upload adapter, SIM commits a permanent row in `content_publish_intent` using an independent, engine-backed session. The primary key is the post ID: only one contender can claim it. An existing intent never authorizes another upload, even after a timeout, exception, cancellation, process loss, caller rollback, edit, reapproval, or retrying a job. This prevents automatic repeated adapter invocation for the same persisted post intent. It does not promise external exactly-once delivery: remote service behavior and transport-internal retries are outside this fence.

The intent has no foreign key or cascade to the post. This preserves evidence after deletion and avoids PostgreSQL key-share locks conflicting with the caller's `ContentPost FOR UPDATE` lock. Intent and receipt commits do not commit caller work. If an independent commit cannot complete, publication fails closed. SQLite can reject an independent write if the caller already holds a write lock; do not resolve this by committing the caller transaction early.

After the adapter returns a nonempty external media ID, SIM separately commits that receipt and timestamp before updating caller-owned post status. If the caller later rolls back, a subsequent eligible publication run restores local status from the matching receipt without calling the adapter. A crash before receipt commit leaves an uncertain intent even if Instagram actually published. Cancellation can leave a threaded adapter still running: the fence is never released.

The fingerprint covers persona/channel IDs, kind, approved caption, media references, JPEG bytes, and a SHA-256 digest of the encrypted account session. No session or credentials are stored in the intent. Session ciphertext is deliberately conservative: even refreshing/re-encrypting a session for the same account blocks automatic receipt reconciliation. A changed account, caption, or file must never be silently attributed to an old receipt. Missing media or an inactive account also blocks automatic reconciliation. Media files must remain immutable while publishing; this patch does not add a filesystem snapshot or external account identity verification.

## Inspect and reconcile

An authenticated owner/superadmin can GET `/api/content/{post_id}/publication`. The read-only response contains `not_attempted`, `uncertain`, or `receipt_known`, attempt time, and any external media ID/publication time. It exposes no fingerprint, session data, or raw adapter errors. History remains inspectable after post deletion. `receipt_known` means the adapter returned an identifier, not that the current caption/media/account matches or that an external verification was performed.

Unknown outcomes require a human to inspect the actual Instagram account and retained receipt evidence. Absence of a receipt or a temporarily missing post is not proof that upload failed. There is no reset, clear, reapproval bypass, or retry endpoint. Editing and reapproving attempted posts is blocked. A new post ID is a new publication decision, not a recovery shortcut: do not copy uncertain posts to evade the fence. Any future explicit reconciliation or new-attempt workflow must preserve history, bind the verified account/payload, and obtain the applicable publication authorization. This patch intentionally offers no automatic recovery for uncertain outcomes.

## Migration and validation

Apply Alembic `0006` before deploying this code, through the normal authorized deployment process. No production migration has been run here. Stop publication workers before schema rollback. Downgrade refuses any nonempty intent table; PostgreSQL additionally takes an exclusive table lock before checking. Never delete intent rows, reset IDs, restore a database snapshot missing recent intents, or drop the table to retry publication. Preserve intent history in backups and recovery plans. An empty-table downgrade/upgrade is tested offline.

Offline tests cover receipts, caller rollback, upload exceptions, cancellation/process-loss simulation, commit failure on both intent and receipt boundaries, concurrent claims (including two stale no-intent reads), payload/account mismatch, edit/reapproval blocks, access control, retained history, and migration upgrade/downgrade. Concurrency tests use file-backed SQLite; PostgreSQL row-lock/deadlock behavior and real crash termination are not integration-tested. No live Instagram, paid API, deployment, production database, or external publication is part of this validation.
