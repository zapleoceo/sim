# Representative admin page patterns

CONFIRMED source at349dc66; rendered feature evidence is recorded in
`tasks/unified-client-admin/visual-validation.md`.

| Pattern | Source | Reusable behavior |
|---|---|---|
| Filtered paginated list | features/clients/clients.component.ts | typed form, list API, filter/reset/page transitions, Material controls |
| Rich operator workspace | features/inbox/inbox.component.* | source badges, message media/guard/delivery, per-dialog composer/pause/resolve, responsive single-pane cutoff1024px |
| Client detail | features/clients/client-card.component.* | identity/header, channel links, stage/status, editable work fields, funnel/partner timeline |
| Scoped metrics | features/dashboard/dashboard.component.* | page header, date/persona filters, loading signal, derived view models |
| Connector setup cards | features/channels/channels.component.ts | persona prerequisite, safe status/error, add/connect wizard and disconnect confirmation |
| Read-only simulation history | features/simulations/simulations.component.ts | typed read API and isolated test conversation viewing |

Layout uses `.sim-page`, `.sim-card`, `--sim-space-*`, Material controls and shared767px phone
strategy. Existing error handlers often return EMPTY; new screens must distinguish error from
empty/loading and provide retry rather than copying indefinite-loading patterns.
