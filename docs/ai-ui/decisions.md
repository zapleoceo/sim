# UI decisions log

Durable, project-wide UI/architecture decisions discovered or made during
`/implement-ui` runs. One entry per decision. Keep one-off task detail in the
task folder, not here.

<!-- Newest first. -->

## 2026-11-11 — admin-rebuild — React + shadcn/ui instead of Angular + Material
- **Decision:** Rebuild the whole admin on React 19 + Vite + TypeScript with shadcn/ui (Radix + Tailwind),
  TanStack Query/Router/Table, react-hook-form + zod. Same routes, roles and API contracts; same build output path.
- **Evidence:** the owner found the hand-rolled Angular layout clumsy; see [../admin-frontend.md](../admin-frontend.md).
- **Scope:** all of `frontend/`.
- **Consequences:** the 2026-11-07 entry's «Material tokens» now means the shadcn tokens in `src/index.css`; its
  identity/reply/audit rules still hold. Persona preview errors are shown, not swallowed.

## 2026-11-07 — unified-client-admin — Historical metadata and source-specific replies
- **Decision:** Reuse Material tokens and shared message presentation across inbox,
  client timeline and activity. Consolidate by existing persona-scoped client identity.
  Reply through a concrete source dialog. Keep persisted message status and individual
  LLM attempts as separate views; unknown metadata remains unknown.
- **Evidence:** `../history/unified-client-admin/backend-contract.md`, `prototype-spec.md`,
  `component-reuse.md`; `backend/sim/api/activity_schemas.py` and frontend activity module.
- **Scope:** Conversation and activity modules.
- **Consequences:** No name-based identity merging, no inferred historical author/delivery
  timestamp, no broadcast reply, no automatic assignment of guard/retry spend to a message.
  LLM audit remains owner-only; raw diagnostics are excluded from activity projections.
