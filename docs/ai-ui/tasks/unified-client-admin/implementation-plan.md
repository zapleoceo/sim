# Unified client administration — implementation plan

Base: `349dc66`, branch `codex/unified-client-admin`. Textual prototype is the user request;
existing Angular19 Material design is the presentation baseline. Preserve PR12 send safety.

## Backend/data

- Implement exact shared contract in `backend-contract.md`: authenticated bounded message
  activity and client-scoped cross-channel history; owner-only persisted LLM attempt log.
- Add nullable per-message operator ID/name snapshot (0008), leave historical unknowns null.
  Expose already-stored model/provider/tokens/cost via shared serialization. Receipt time
  is read-only and nullable; never infer model-call/message relationships by proximity.
- Dedicated query repository/service, SQL filtering/count/pagination, deterministic order,
  shared date bounds, escaped substring search. Sanitize new activity `error` projections:
  generic status only, no raw provider errors or credential-bearing URLs. Existing API
  compatibility preserved; do not weaken roles, CSRF, delivery receipts or channel identity.
- Backend agent owns backend application/migration/tests and relevant API contract docs.

## Frontend/state/presentation

- Typed additive models/API methods matching the contract; reusable rich message metadata/card.
- Client card gets unified conversation across its linked dialogs, newest page rendered in
  chronological order, bounded paging/refresh/filtering, source-specific reply link. Keep
  funnel/partner history and editable client details separately usable.
- Global `/activity` screen with Messages and owner-only LLM calls tabs, real server filters,
  page totals, explicit loading/empty/error/retry and mobile filter sheet.
- Make activity discoverable in navigation and channel context; add clear client history
  links and compact model/token/author metadata to existing inbox without rewriting sends.
- All role restrictions remain enforced by backend; errors never become fake empty success.
- Frontend agent owns frontend source/tests and follows prototype/reuse/DNA documents.

## Validation and rollout boundary

- Backend targeted then full pytest, Ruff, changed-module typing; migration up/down on disposable
  SQLite; baseline Windows symlink failure/full mypy errors remain separately reported.
- Frontend production build, ESLint, spec typecheck and ChromeHeadless unit tests.
- Root prepares isolated local demo DB using existing seed helpers, no real secrets and no
  worker; visual reviewer captures actual routes/interactions and responsive boundaries.
- Review final diff, fix concrete defects, update docs/state/evidence and deliver reviewable
  branch. No deployment or real connector accounts/sends. No blanket unrelated refactor.

## Knowledge context

Vera search `Sim единая лента сообщений клиента журнал активности коннекторы` returned
event547924 (2026-11-05 MVP/admin/Telegram-auth summary); only confirms historical scope.
Current repository is authoritative. Other search hits were unrelated.
