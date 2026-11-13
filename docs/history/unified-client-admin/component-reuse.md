# Component reuse decisions

Evidence: repository source at349dc66; no existing global activity page or client message-feed component.

| Need | Reuse | Decision / boundary |
|---|---|---|
| Page title/actions | `shared/page-header.component.ts` | Reuse on activity; client identity header remains bespoke |
| Desktop/mobile filters | `shared/filter-bar.component.ts` + FiltersTemplateDirective | Reuse same form in inline controls and MatBottomSheet |
| Phone detection | `shared/breakpoints.ts` | Reuse767px cutoff; no separate resize listener |
| Channel provenance | `shared/channel-icon.component.ts`, CHANNEL_LABELS | Reuse for all four kinds; add visible name/account text |
| Client identity | `shared/avatar.component.ts` | Reuse initials/broken-image behavior |
| Client state | StatusBadge, StageChip, NeedsOperatorBadge | Reuse semantics; do not invent delivery enum in these components |
| Guard detail | `shared/guard-badge.component.ts` | Reuse explicit generator-vs-guard model distinction |
| Empty states | `shared/empty-state.component.ts` | Reuse display; pair retry/reset actions externally |
| Date/time formatting | `shared/format.ts` | Reuse consistent display; expose timezone/created-vs-sent meanings |
| Date/time filter | DatetimeField/Material date controls | Reuse without converting nullable token/time into fake defaults |
| Pagination | MatPaginator + `shared/paginator-intl.ts` | Reuse localization, total/page-size semantics |
| Message body/media | Existing inbox template | Extract focused shared rich-message component/helper; preserve attachments/guard/delivery |
| API filtering | `core/api/http-params.ts` | Extend typed DTO/API boundaries, not duplicated query-string concatenation |
| Connection setup | ChannelsApi + ChannelWizard | Preserve implementation; add navigation to real logs only |

Do not copy entire InboxComponent to create client feed. It also owns composer, selection,
polling, unread markers and pause state. Extract only rendering/metadata helpers compatible
with both views. Keep per-dialog reply control in inbox; a consolidated feed must not broadcast.
New delivery badge/presentation is justified if it replaces repeated inbox/activity mapping.
No production mock arrays; test fixtures stay in specs/local demo tooling.
