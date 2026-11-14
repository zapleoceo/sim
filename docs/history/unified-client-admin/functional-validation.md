# Functional validation

2026-11-07, local branch `codex/unified-client-admin`, base `349dc66`.

## Backend

- Author targeted run: 133 passed, including 36 new cases. Independent review required
  mixed AND predicates; those were added for both message and LLM lists.
- First full integration run: 569 passed, two failures. One was the known Windows symlink
  privilege error (WinError1314). The second revealed test isolation: the new migration
  test invoked Alembic logging configuration before conversation tests and disabled their
  logger. Fixed in the test harness, preserving the application assertion and production
  migration environment. Ordered migration/conversation/activity regression: 53 passed.
- Final full suite: **572 passed, one known environment failure**, 24 Alembic warnings,
  113.43 seconds. Only `test_unsafe_local_media[symlink]` fails with WinError1314 before
  application behavior runs. The suite exit code is 1; this is not a fully green suite.
  Raw output: ignored evidence `backend-full.txt`.
- Full Ruff: passed. Full mypy: 18 existing errors in six unchanged modules; no new errors.
  Five touched backend modules pass focused mypy. Full typing is not claimed clean.
- Migration up/down is verified on disposable SQLite; PostgreSQL execution/load plans
  and live connector behavior remain outside this local validation.

## Frontend and runtime

- Final frontend source: production build `main-54DL3F47.js`, ESLint, spec typecheck
  and **91/91 ChromeHeadless tests** pass. See `frontend-validation.md` for commands.
- Isolated localhost18400 uses synthetic SQLite data, no environment/dotenv secrets,
  empty connector registry and no worker. Existing seed plus two-channel client1 scenario:
  1527 messages, named operator, known zero tokens/cost and independently recorded receipt.
- No production deployment, real connector connection, message send or external LLM call.
- Actual rendered results are tracked separately in `visual-validation.md`; unit tests
  and compilation alone are not visual or live external acceptance evidence.
