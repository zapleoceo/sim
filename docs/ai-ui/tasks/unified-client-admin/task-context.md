# Unified client administration

## State

- 2026-11-07 Asia/Saigon; D:/Projects/Sim; branch codex/unified-client-admin;
  base origin/main 349dc6648fb6808ff18c982260b40d138e3244e6. Clean tree before start.
- User requests cohesive refactoring, a unified cross-channel client conversation showing
  source, author, timestamp, model and tokens, and an activity log with useful filters;
  functional admin modules ready to connect channels.
- Existing outbound safety/readiness PR12 must remain intact. No production deploy or real
  channel connections/sends are authorized in this task. Prior merge permission was for P1s;
  new work will be kept reviewable on its own branch unless scope is extended.
- Prototype source: user's textual request and existing Material admin conventions.
- Reuse existing client identity boundaries (persona-specific client). Do not merge people
  by names; do not invent historical operator/model/token details.
- Preserve current inbox, reply/pause/funnel/roles and connector settings. New data views
  must use real backend endpoints. Local isolated demo data allowed for visual checks only.
- Current phase: complete locally; independent C–F PASS within documented limits.
  Backend final full suite: 572 pass,
  one known Windows symlink privilege failure; Ruff clean, full mypy 18 baseline errors.
  New combined filter cases and migration-test logging isolation are verified. Frontend
  build/lint/spec typing and full 91/91 ChromeHeadless tests pass.
  Rendered client-form displacement, crowded mobile inbox CTA and wrong fragment navigation
  corrected and rechecked. Visual PASS for tested local scenarios: 12 widths, 132 numeric
  assertions, zero failed/overflow. Filters, paging, role denial and source links verified.
  Independent reviewer inspected final combined diff and three final screenshots; no blocker.
  DOT subsequently requested a checkpoint/push for cloud continuation before laptop reboot.
  Feature source committed and pushed as `14bda0ca357c4390a691510cd9c2713fdca8f360`.
  Synthetic localhost18400 server PID38804 stopped; no worker/test run remains active.
  Next: cloud handoff in `cloud-handoff.md`; no merge/deployment authorized or performed.
- Shared adapter: unregistered SIM. Existing FastAPI/uv + Angular19 conventions govern.

## Scope assumptions

Refactor the touched workflows, shared message presentation/query services and metadata
boundaries rather than unrelated adapters. Provide desktop/mobile client timeline, searchable
activity view, source/direction/author/delivery/date filters and clear loading/empty/error states.
Distinguish business message activity from LLM/job operational records; never expose secrets.
Live connector readiness is not proven by an admin screen or local fixture.
