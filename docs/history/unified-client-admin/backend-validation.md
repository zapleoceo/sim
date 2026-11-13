# Backend implementation and validation

2026-11-07; branch `codex/unified-client-admin`, base `349dc66`. Local evidence only.

Implemented exact `backend-contract.md` endpoints and additive message metadata. New
activity message errors use a generic status; LLM errors expose only `has_error`. SQL
repository shares context filters, escaped literal substring search and bounded count/page
queries. UTC/date bounds are shared with existing reports. New manual sends snapshot a
known admin; historical/phone/unknown author attribution remains null. Migration 0008
adds nullable fields and message ordering indexes. Receipt reads never write PR12 state.

## Verification

- `uv run --frozen pytest tests/test_activity.py tests/test_activity_migration.py tests/test_api_smoke.py tests/test_admins.py tests/test_worker.py tests/test_ai_send_cancel.py tests/test_outbound_receipts.py tests/test_analytics.py tests/test_migrations.py -q --tb=short`
  — **133 passed**, 24 existing Alembic path_separator deprecation warnings, 26.00 seconds.
- New activity/migration cases: **36 tests**. Auth/roles; stable tie ordering; counts and
  offset; literal `%`, `_`, backslash; all filter groups; date-only/UTC-offset/exclusive
  datetime; invalid enums/ranges/limits; separate same-name clients/personas; archived
  channels; unknown client; null vs zero metadata; unscoped LLM records; no raw diagnostic
  secrets; immutable renamed/deleted author snapshot; unknown author; receipt retention.
- SQL instrumentation verifies one count and one LIMIT/OFFSET query, without N+1 reads.
- Disposable SQLite migration 0007→0008→0007→0008 preserves legacy message and durable
  receipt, verifies null historical author, SET NULL foreign key and added indexes.
- `uv run --frozen ruff check sim tests/test_activity.py tests/test_activity_migration.py`
  — clean after import formatting.
- `uv run --frozen mypy sim/adapters/db/repositories/activity.py sim/api/activity_schemas.py sim/api/routers/activity.py sim/api/date_bounds.py sim/services/outbound.py --follow-imports=silent`
  — success, five files. This is touched-code checking, not a claim that baseline full
  project mypy is clean.
- `git diff --check` — clean (Git prints existing Windows line-ending conversion notices).

## Boundaries

Parent performs full backend/frontend suites, independent review and rendered integration.
No commit/push, production calls, live connector sends or external LLM calls performed.
PostgreSQL migrations/query plans and production-volume latency were not exercised.
Offset pagination has deterministic order within a read; pages are not a transactional
snapshot when new messages arrive. Search/count cost can grow with retained history.
`docs/api.md` marks this feature as a local candidate, not deployed capability.

## Independent review follow-up

- Added combined AND-filter regression cases for messages and LLM: simultaneous context,
  dates, text and type/status filters; same filtered count across page offsets; conflicting
  predicate yields zero count/items instead of an OR union.
- Repeated `uv run --frozen pytest tests/test_activity.py tests/test_activity_migration.py -q --tb=short`:
  **38 passed**, four existing Alembic warnings, 8.92 seconds. New test Ruff and diff check pass.
- Updated stale API baseline notices to main `349dc66`, PR #1–#12; queue fencing is merged,
  unified admin remains a local candidate, production status remains unconfirmed.
- Full-suite integration exposed Alembic `fileConfig` disabling application loggers after
  the new migration test. The new test now retains loaded options but skips logging
  reconfiguration (`cfg.config_file_name = None`); production migration behavior and
  conversation assertions are unchanged. Ordered regression:
  `uv run --frozen pytest tests/test_activity_migration.py tests/test_conversation.py tests/test_activity.py -q --tb=short`
  — **53 passed**, four existing warnings, 12.01 seconds. Both new test files pass Ruff.
