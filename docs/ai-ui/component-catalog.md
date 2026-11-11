# UI component catalog

React admin (`frontend/src`). shadcn/ui primitives live in `components/ui` (button, input, textarea, label, badge, card,
select, switch, dialog, sheet, alert-dialog, dropdown-menu, tabs, tooltip `Tip`, table, skeleton). App components:

| Component | Path | Contract / reuse |
|---|---|---|
| Page, PageHeader, BackLink | components/page.tsx | page width (narrow/default/wide), title/subtitle/actions, «← Все …» link |
| EmptyState, ErrorState, InlineError | components/empty-state.tsx | no-data vs load error with «Повторить»; inline `role=alert` text |
| Avatar | components/avatar.tsx | name/url/size; photo or initials |
| ChannelIcon, StageChip, StatusBadge, NeedsOperatorBadge | components/badges.tsx | channel kind, funnel stage colour, client status, human-attention marker |
| GuardBadge | components/guard-badge.tsx | guard verdict, violations, original draft, guard model |
| MessageContent, MessageMetadata | components/message-parts.tsx | media/text/guard/delivery failure; operator name or generator, tokens, cost |
| MessageCard | components/message-card.tsx | one stored message in activity/client feeds |
| Field, FormGrid | components/field.tsx | label + control + hint/error with aria wiring; two-column form grid |
| OptionSelect | components/option-select.tsx | typed select over number/string/null values (Radix forbids empty values) |
| FilterBar, SearchInput, FilterField | components/filter-bar.tsx | search + filters inline on desktop, bottom sheet on phones, active count and reset |
| DataTable | components/data-table.tsx | TanStack Table rendering, skeleton, empty state, per-column `meta.className` |
| Pager | components/pager.tsx | server pagination for `{limit, offset}` lists, 25/50/100 |
| ConfirmProvider/useConfirm | components/confirm.tsx | `await confirm({...})` for destructive actions |
| RoutePending, RouteError | components/route-states.tsx | lazy chunk progress line; crashed page with reload |
| AppShell | layouts/app-shell.tsx | sidebar (collapsible rail), bottom bar + «Ещё», theme and logout menu |

Feature-local components stay in `features/<screen>/`. Do not duplicate the shell, filter sheet, identity badges or HTTP error handling.
