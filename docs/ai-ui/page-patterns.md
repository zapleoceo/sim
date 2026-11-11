# Page patterns

React admin reference screens (`frontend/src/features`):

| Pattern | Reference | Notes |
|---|---|---|
| Filtered paginated table | clients/clients-page.tsx | FilterBar, DataTable, server Pager, filter change resets page |
| Rich operator workspace | inbox/inbox-page.tsx (+ inbox-list, conversation, inbox-logic) | 5 s silent polling, source badges, media/guard/delivery, composer, AI pause/resolve, single pane below 1024px |
| Client detail | clients/client-page.tsx | header + channel links, stage stepper/status, edit form, funnel/partner timeline, scoped activity feed |
| Scoped metrics | dashboard/dashboard-page.tsx | presets and dates, persona filter, KPI cards, Recharts, by-channel table |
| Editor page with schema | personas/persona-page.tsx, strategies/strategy-page.tsx | react-hook-form + zod schema module, confirm delete, `/new` → `/$id` after create |
| Connector setup | channels/channels-page.tsx + channel-wizard.tsx | persona prerequisite, safe status/error, dialog wizard with step logic module |
| Read-only history | simulations/simulations-page.tsx | load-more list and transcript panel |

Shared building blocks: see [component-catalog.md](component-catalog.md); tokens and layout: [design-dna.md](design-dna.md).
