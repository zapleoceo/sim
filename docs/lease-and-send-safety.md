# Job preflight, lease renewal, and external send outcomes

## Scope

The investigation baseline is main `ccb75db385927cfa3484a28e6c1e55238aab8652`.
GitHub showed no open pull requests at the initial check. All 143 available tracked
backend/infra files matched that commit's Git blob hashes. No production or real
channel was used. This document describes a local candidate, not a deployed feature.

The candidate adds only a read before handler execution. It checks the job ID,
`status = running`, and the claim's captured `attempts` generation. A known stale,
terminal, cancelled, or deleted claim is skipped. The independent read session closes
before the handler starts. A read error or cancellation propagates without invoking
the handler, settlement, retry scheduling, or giveup; the existing lease recovery can
claim the job later. An expired but unreclaimed owner is still allowed to run.

This check is not atomic with handler execution. A reclaim after the read, or while
the handler is already running, can still cause duplicate external work. The existing
generation-fenced completion/failure behavior is unchanged. No heartbeat, handler
cancellation on lease loss, retry-policy change, migration, or new delivery state is
included. In particular, this change does not provide exactly-once delivery.

## Verified current behavior

- Worker slots are limited to four per process. The lease is calculated once at
  claim time from model budgets. Default settings yield 2,220 seconds (37 minutes).
  This budget is an estimate, not a hard timeout on a complete handler.
- The job is claimed and committed before dispatch. Handler work then runs inside
  a separate transaction, including external I/O. Completion/failure is a third
  transaction and uses the captured generation.
- The database pool for a non-SQLite engine is five connections plus five overflow
  connections. Handler sessions, polling, short claims/settlements, separate LLM
  audit writes, and publication intent/receipt writes may overlap. A heartbeat would
  share this finite capacity unless deliberately designed otherwise.
- SQLite permits only one writer at a time. An independent renewal UPDATE can be
  blocked by a handler's flushed write to any row, even one unrelated to that job.
  A heartbeat must not be tested only with handlers that never write before waiting.
- Claim expiry uses the process's naive-UTC wall clock, whereas dispatch waiting uses
  the event loop's monotonic clock. A monotonic heartbeat schedule alone would not
  remove wall-clock skew between workers or jumps of the wall clock.
- `OutboundService.deliver()` calls the connector before recording `SENT` and its
  external ID. A timeout, cancellation, or later transaction rollback can leave a
  delivered message recorded as `QUEUED`. The worker currently retries send failures.
- `SendResult` contains only `ok`, optional external ID, and optional error. It does
  not distinguish a definitive no-effect rejection from an uncertain network result.
  `ChannelPort.send_text()` has no stable operation/idempotency-key parameter.
- Instagram direct messages call synchronous `direct_send` through
  `asyncio.to_thread`. Cancelling the awaiting coroutine does not stop that thread.
  Cancelling an HTTP coroutine likewise cannot prove its request was never accepted.
- Instagram content upload has a separate durable intent/receipt protocol. That
  protection is specific to content publishing, not ordinary messages. Admin simulator
  replies also call Telegram directly and need separate operation identities if added
  to an eventual send-safety protocol.

## Deterministic offline evidence

The investigation module is intentionally named `tests/lease_risk_reproductions.py`,
outside the default `test_*.py` collection. Run explicitly from backend:

```
uv run pytest tests/lease_risk_reproductions.py -q -rx
```

The repository fixtures and these scenarios use injected fakes, not real accounts.
For this investigation, a separate local validation harness additionally removed
external-service environment settings and rejected internet socket connections. That
`offline_run.py` harness is not a tracked repository file or required by the command
above. Effects in these scenarios are in-memory fakes, not real sends. Known
gaps are strict expected failures of the desired safety properties. Only the dedicated
`SafetyGap` assertion type is expected; setup, cleanup, and unrelated assertion failures
still fail normally. Expected failures are not passing safety tests.

Scenarios:

1. Fake sender accepts a message and loses the response. Ordinary retry sends twice.
2. Fake send succeeds, but the handler transaction fails at commit. Retry sends twice.
3. Fake send has been accepted when the worker task is cancelled. Its lease is later
   reclaimed and the replacement sends the message again.
4. A claim already superseded before handler start is skipped by this candidate.
5. A reclaim immediately after a successful preflight read still permits two effects.
6. The real Instagram transport wrapper, with a fake synchronous client, finishes its
   thread's send after the awaiting task has been cancelled.
7. A SQLite writer prevents an independent generation-guarded renewal UPDATE. This
   diagnostic uses a zero busy timeout, so it is bounded and does not rely on sleep timing.

The baseline reproduced five gaps before the candidate, consistently over 15 runs.
The candidate fixes only the already-superseded-start scenario. The newly explicit
post-read race remains a gap, along with the four external-outcome/cancellation gaps.
The regular `test_job_preflight.py` regressions exercise the new preflight behavior.

## Alternatives and exact boundary

### A. Database-only heartbeat and diagnostics

This could reduce overlap when a healthy, active worker exceeds its estimated lease.
It is an availability mitigation, not a guarantee against repeated external actions.
If developed separately, its minimum constraints are:

- A short independent transaction updates only `locked_until`, guarded by ID,
  `running` status, and captured generation. Never increment attempts, revive a
  terminal job, clear errors, or change retry/backoff/max-attempt semantics.
- Only a committed update establishes a renewed deadline. A commit timeout is
  uncertain, not proof that renewal failed. Guard against shortening an existing
  deadline after a wall-clock jump or accepting a delayed/stale renewal timestamp.
- Bound pool acquisition, database lock/statement waiting, and the transaction.
  Specify a renewal interval and a finite renewal horizon; otherwise an unresponsive
  handler can retain its lease forever. At the horizon, do not silently turn an
  unknown external action into a retry or cancelled handler.
- Run at most one renewal operation per job at a time. Supervise and observe the
  heartbeat task; stop and await its cleanup on handler completion, error, or parent
  cancellation. Heartbeat failure must not schedule a second heartbeat indefinitely
  or leave unobserved exceptions/connections behind.
- On lost generation, stop renewing that generation and report it. On database
  uncertainty, report it. Do not equate either condition with permission to retry an
  external operation, run giveup, or cancel an in-flight sender.
- Prove contention and cleanup on PostgreSQL, including pool exhaustion and blocked
  statements. A SQLite suite cannot validate PostgreSQL row-lock/commit semantics.

Such behavior still lets an old handler continue after a new claim exists. Increasing
the lease or keeping renewal best-effort cannot resolve that limitation. No heartbeat
has been implemented in this candidate.

### B. Fail-stop on lost lease

Stopping before a side effect is initiated can be safe when ownership is known to be
lost. The preflight is a limited example. Generic `task.cancel()` after a send may have
started is not a safe fail-stop protocol: it can roll back the only receipt while the
remote action completes. Preventing subsequent action needs cooperative checks at
each boundary, and preserving an in-flight outcome needs a durable action record.

Without those records, marking the job failed or suppressing all retries changes
delivery policy and may silently lose messages. It must not be presented as a narrow
lease fix.

### C. Durable send intent with explicit unknown-outcome handling

Recommended next implementation scope is ordinary `SEND` only, in a separate change:

1. Define a stable operation identity for the message, recipient/account, and payload.
   Commit a unique dispatch intent before the first possible network effect, outside
   any rollback scope of the handler. A process crash after intent but before send is
   intentionally conservative: the intent alone is not proof of delivery or no effect.
2. A competing or reclaimed worker must not dispatch an existing in-flight/unknown
   intent. Use a provider idempotency key only if that provider's contract supports it.
   Do not assume a job ID, local dedup key, or matching text deduplicates remote sends.
3. Persist definitive success and receipt in a short independent transaction. Tie the
   receipt to the stable operation, so a valid late result is not discarded merely
   because the queue generation changed. Queue settlement remains generation-fenced.
4. Distinguish definitive rejection with no remote effect from ambiguous failures.
   Timeouts, cancellation, missing acknowledgments, receipt-write failure, and crash
   during dispatch must be reconciliation-required, not automatically resent.
5. Expose that state to the operator. Reconcile using reliable provider identity/history
   where supported; otherwise require an explicit resolution with an audit record.
   Never clear an intent or recreate the message under a new ID as an automatic retry.
6. Preserve pause/cancel checks before dispatch and define how cancellation during
   dispatch is displayed. Include chunked sends and partial receipts in the contract.

This requires new persistent protocol, recovery, and API/UI behavior. Reusing arbitrary
job JSON/status values could avoid a literal migration, but would not remove the
protocol or recovery decisions. It is outside the current no-migration narrow change.

The owner decision is the delivery trade-off: after an ambiguous send, should SIM
hold it for reconciliation even if it may never have reached the recipient? That is
the recommended safe default for the proposed protocol. It differs from current
automatic retries and needs an explicit product decision and an operator workflow.
The decision is not required for the limited stale-start preflight itself.

## Validation and rollout limits

The implementation is tested separately from these known-gap reproductions:

- Baseline default suite: 502 passed, with 16 existing Alembic warnings.
- Candidate default suite: 525 passed, with the same 16 Alembic warnings.
- Focused preflight/fencing/worker/dispatch suite plus explicit reproductions:
  76 passed and 5 strict expected failures of unresolved safety properties.
- Explicit reproductions alone: 2 passed and 5 strict expected safety failures.
- Ruff, compileall, patch dry-run, and whitespace checks passed.

All validation used the local network-blocking harness or static tools. The standard
repository commands for the regular suite and lint are `uv run pytest -q` and
`uv run ruff check sim tests`. PostgreSQL integration, live connectors, and deployment
were not run. An expected safety failure is evidence of a remaining problem, not a
successful delivery-safety test.

Before any separately authorized release: verify final commit tests and PostgreSQL
behavior, drain/replace old workers, and ensure no mixed-version worker bypasses the
new preflight. Old workers can still start already superseded work; no statement here
asserts production currently runs either generation. The candidate cannot repair or
automatically reconcile historical sends of unknown outcome.

Source files at the baseline commit:

- [Worker](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/worker/loop.py#L26-L117)
- [Queue](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/adapters/db/repositories/jobs.py#L77-L148)
- [Transactions/pool](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/adapters/db/session.py#L17-L37)
- [Outbound](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/services/outbound.py#L178-L218)
- [Instagram thread](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/adapters/channels/instagram.py#L148-L151)
- [Publication intent](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/services/instagram_content.py#L85-L122)
- [Admin simulator](https://github.com/zapleoceo/sim/blob/ccb75db385927cfa3484a28e6c1e55238aab8652/backend/sim/services/admin_bot.py#L375-L418)
