# Durable outbound attempts and API readiness

## State

- 2026-11-07, Asia/Saigon. Implementation in `D:/Projects/Sim`, branch
  `codex/fix-delivery-readiness`, base `ccb75db385927cfa3484a28e6c1e55238aab8652`.
- Owner: this SIM session, sole implementer per owner-authorized DOT request.
- Clean checkout verified; no other worktrees or local changes. Shared router has no SIM adapter.
- Scope: durable outbound send fence/receipt, fail-closed readiness and its Docker/deploy gates.
  Publication and merge authorized by DOT; no deployment, production writes or settings changes.
- Evidence before changes: duplicate fake-channel send after rollback reproduced; DB outage
  returned HTTP 200. Baseline: 501 tests pass, one Windows symlink privilege failure;
  Ruff passes, mypy reports 18 errors in six pre-existing files.
- Implemented: migration 0007, permanent send fence/receipt, terminal uncertain outcomes,
  operator flag, readiness/liveness endpoints and shared Docker/deploy gate; docs updated.
- Checks: focused intent/readiness/migrations 39 passed; full suite 534 passed, one existing
  Windows symlink privilege failure, 20 Alembic warnings. Final full rerun confirmed the
  same 534 passed / one environment failure in 96.12 s; no tests excluded or altered to hide it.
  Targeted outbound/readiness/worker/cancellation regression: 52 passed. Ruff and shell syntax pass.
  Touched-module mypy (8 files, follow-imports=silent) passes; full mypy retains the same
  baseline 18 errors in six untouched files. PostgreSQL and real channel delivery unverified.
- DOT additionally authorized merge after ordinary checks; no production deployment.
- Next: publish PR, inspect remote checks and merge eligibility. No test workflow exists on
  the base; GitHub rules/branch-protection reads return a plan-limitation 403. Merge must
  use normal server enforcement without admin override. No frontend code changed.

## Context from KB

Audit searched Vera for `sim.zapleo.com проект Sim деплой архитектура`; no relevant hits.
Primary context: current `docs/operations.md`, `docs/publication-intents.md`, outbound service,
Instagram durable publication intent, worker generation fencing (PR #11), and local reproductions.
SIM's FastAPI/uv and local documentation conventions override unrelated itstep Flask/PDM rules.

## Plan and contract

1. Add permanent per-message intent and independent success receipt (migration 0007).
   A unique message key fences concurrent attempts; bind it to a payload/account fingerprint.
   Uncertain outcomes are never automatically sent again; flag the dialog for reconciliation.
   Known receipts recover local state after rollback without repeating network I/O.
2. Add DB-bounded `/api/ready`, dependency-free `/api/live`; retain `/api/health` diagnostics
   with HTTP 503 when DB is unavailable. Use readiness in image, Compose and deploy smoke.
3. Regression tests with fake transports and failure injection; local migration roundtrip,
   full backend suite/Ruff, touched-code typing and shell checks. Preserve baseline failures.
4. Update affected contracts and operational limitations; commit, push, open PR and merge
   after checking the remote SHA, mergeability and available checks (no admin override).

The delivery choice prioritizes avoiding duplicate outbound sends over automatic delivery:
crash after intent commit but before send may leave an unsent message for human reconciliation.
No claim of provider exactly-once. No retry/reset endpoint or automatic deletion of intents.
Old workers must be fully stopped before rollout; preserve intent history in backups/restore.
