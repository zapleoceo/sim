# Independent quality review

Reviewer: `/root/admin_quality_review`, independent of implementation authors.
2026-11-07; worktree `D:/Projects/Sim`; branch `codex/unified-client-admin`;
base `349dc6648fb6808ff18c982260b40d138e3244e6`. Final frontend bundle: `main-54DL3F47.js`.

## Scope and evidence

Read shared workflow and adapter router (unregistered SIM), task context, backend contract,
complete tracked backend/frontend diff and relevant untracked source/tests/migration, auth
dependencies, ORM context, existing send/client/inbox flows, Design DNA and validation docs.
Ran own `git status`, `git diff`, `git diff --check`; no whitespace errors, only Windows CRLF
normalization notices. Initial clean tree is recorded in task context; concurrent agent/root
edits are workflow changes. No application files changed by this reviewer.

Backend: auth boundaries preserved; count/page share SQL predicates, deterministic order and
bounded results; joins do not multiply rows or add per-row queries. Manual sends capture a
nullable immutable operator snapshot; receipt reads do not mutate PR12 protection. Generic
activity errors and LLM has_error avoid raw diagnostics; unscoped calls retain null context.
Migration is additive/reversible, shared date parser preserves analytics semantics.

Frontend: shared content/metadata/feed components remove duplication; typed API contracts
match server. switchMap cancels obsolete requests; invalid filters cancel subscription;
filter/refresh resets pagination; false LLM outcome survives serialization. Owner-only call
requests are gated before execution. Client-route reload clears the old card and recreates
its feed. Existing forms/history remain before long conversation. Native Material controls,
labels, focus conventions, status/alert regions and responsive filter sheet are reused.
No new global/deep/important overrides, dependencies, untyped any or mock production path.

## Resolved findings

- Added combined AND message/LLM regression tests, conflicting predicate and count/page
  agreement (`backend/tests/test_activity.py:203`).
- Refreshed stale main/fencing statements in `docs/api.md`.
- Isolated Alembic test logging (`cfg.config_file_name = None`), preserving later logger tests.
- Removed dead extracted-media/blocked/text selectors from inbox stylesheet.
- Author border now uses spacing token. Existing 12px metadata is documented in Design DNA;
  image height retains established 280px limit. Colors, gaps and radii use existing tokens.
- Operator-facing copy correctly identifies the LLM journal as owner-only.
- Long timeline no longer pushes existing client forms/history below thousands of pixels.
- Inbox client-history link moved outside crowded mobile header.
- Broken plain fragment link replaced with local scrollIntoView; inbox fragment handled
  after client render. Added regression tests and requested actual rendered click verification.

## Verification

- Backend full output inspected: **572 passed, 1 known WinError1314 symlink environment
  failure**, 24 Alembic warnings; exit1, not a fully green suite. The new test-order failure
  was corrected and full suite rerun. Full Ruff passes; touched mypy passes, full mypy has
  18 existing errors in six baseline modules. See `functional-validation.md`.
- Final frontend **91/91 ChromeHeadless tests**, production build, spec typecheck and lint
  pass. Read final command evidence and added client/API/filter/metadata/role tests.
  See `frontend-validation.md`.
- Read final `visual-validation.md` and independently inspected exact saved final images:
  `actual/1440-activity-final.png`, `actual/390-inbox-final.png`, and
  `actual/390-client-conversation-final.png`. Desktop sources/metrics are legible; mobile
  header/CTA no longer overlap, conversation cards wrap within the viewport. Numeric oracle
  reports 132 assertions with zero failures across 12 widths. Visual reviewer verified
  filter intersections/reset/pagination, role restrictions, channel/source links and jump.
  Stale-frame capture artifacts were recaptured; they are not accepted as final evidence.
- Final source check is against uncommitted working-tree changes on base349dc66; root will
  record the eventual local commit. Approval does not imply push, merge or deploy permission.

## Gates C–F

| Gate | Status | Reason |
|---|---|---|
| C Implementation | PASS | Required states/flows represented, scoped changes, final production build passes. |
| D Functional | PASS with recorded baseline limitations | Final frontend tests/typing/lint, backend evidence and rendered routes/interactions reviewed; known Windows test failure and baseline mypy explicitly retained. No new blocking runtime error reported. |
| E Visual | PASS for tested scope, limits recorded | Correct final images inspected; 12-width numeric/token/overflow matrix passes, correction loop completed. No independent pixel reference or exhaustive media/network-error/cross-browser visual coverage. |
| F Quality | PASS | Final combined code diff reviewed; identified duplication/token/copy/navigation regressions corrected; no remaining code blocker found. |

## Remaining risks and limits

Offset pages are not insert snapshots; substring/count cost and PostgreSQL migration/query
plans were not load-tested. External connector delivery, real credentials and production
deployment were not exercised. Universal raw webhook/action audit and automatic cross-persona
identity merging are not provided. Text-only prototype means no independent pixel reference;
visual conformance is structural/token/responsive, not a pixel-perfect claim. Exact rollback
loses the newly added operator snapshot columns, as expected for schema downgrade.

**Final verdict:** no remaining blocking issue found in the reviewed change. C–F pass within
the explicitly recorded local validation boundaries. This is not production/live connector
acceptance, an exhaustive accessibility audit, or a fully clean baseline typing/test claim.
