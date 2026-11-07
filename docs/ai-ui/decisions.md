# UI decisions log

Durable, project-wide UI/architecture decisions discovered or made during
`/implement-ui` runs. One entry per decision. Keep one-off task detail in the
task folder, not here.

<!-- Newest first. -->

## 2026-11-07 — unified-client-admin — Historical metadata and source-specific replies
- **Decision:** Reuse Material tokens and shared message presentation across inbox,
  client timeline and activity. Consolidate by existing persona-scoped client identity.
  Reply through a concrete source dialog. Keep persisted message status and individual
  LLM attempts as separate views; unknown metadata remains unknown.
- **Evidence:** `tasks/unified-client-admin/backend-contract.md`, `prototype-spec.md`,
  `component-reuse.md`; `backend/sim/api/activity_schemas.py` and frontend activity module.
- **Scope:** Conversation and activity modules.
- **Consequences:** No name-based identity merging, no inferred historical author/delivery
  timestamp, no broadcast reply, no automatic assignment of guard/retry spend to a message.
  LLM audit remains owner-only; raw diagnostics are excluded from activity projections.
